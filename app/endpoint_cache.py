"""
Shared in-process cache for Pexip endpoint registration/availability data.

Pexip documents a Management API limit of 1,000 requests per 60 seconds.
This module reduces redundant calls by caching list_registered_endpoints()
results within each Gunicorn worker process.

The cache is attached to the Flask application instance so that:
  - In production: one app per Gunicorn worker → one cache per worker (shared
    across all threads and browser requests handled by that worker).
  - In tests: each test creates a fresh Flask app → fresh cache, no bleed.

NOTE: With --workers 2, each worker process holds its own independent cache.
At most 2 Pexip refreshes can occur per TTL window (one per worker), vs.
one per browser client without caching.  A cross-process store (Redis) would
reduce this to 1 per TTL but is not required at typical deployment scale.
"""
import logging
import threading
import time

from app.config import Settings

logger = logging.getLogger(__name__)


class EndpointCache:
    """Thread-safe in-process cache for registered endpoint data."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = None          # None = never successfully populated
        self._fetched_at = None    # monotonic timestamp of last successful fetch

    def get(self, fetch_fn):
        """Return cached endpoint list, refreshing from Pexip when the TTL has elapsed.

        Thread-safe within a single worker process.  Only one thread refreshes
        at a time; others wait on the lock rather than each issuing their own
        Pexip request (stampede prevention).

        On refresh failure:
          - If a previously successful result exists, return stale data and log.
          - If no result has ever been cached, re-raise so callers can surface an error.
        """
        now = time.monotonic()
        with self._lock:
            ttl = Settings.ENDPOINT_STATUS_CACHE_SECONDS
            if self._data is not None and self._fetched_at is not None:
                age = now - self._fetched_at
                if age < ttl:
                    return self._data  # cache hit — no per-hit logging

            # Cache is empty or expired.  Holding the lock means only one thread
            # refreshes; all other threads queue and receive the new result.
            try:
                fresh = fetch_fn()
                self._data = fresh
                self._fetched_at = time.monotonic()
                logger.info(
                    "Endpoint cache refreshed: %d registered endpoints (TTL=%ds)",
                    len(fresh),
                    ttl,
                )
                return fresh
            except Exception as exc:
                if self._data is not None:
                    stale_age = now - self._fetched_at if self._fetched_at is not None else 0
                    logger.warning(
                        "Pexip endpoint refresh failed (%s); returning stale cached data "
                        "(%d endpoints, cache age %.0fs)",
                        exc,
                        len(self._data),
                        stale_age,
                    )
                    return self._data
                logger.error(
                    "Pexip endpoint refresh failed and no cached data available: %s", exc
                )
                raise
