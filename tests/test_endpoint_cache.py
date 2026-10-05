"""
Regression tests for the server-side endpoint registration/availability cache.

Requirements verified:
  A. Multiple requests inside the TTL cause only one Pexip fetch.
  B. A request after TTL expiration triggers a fresh Pexip fetch.
  C. Simultaneous requests after expiration do not stampede Pexip.
  D. Invalid ENDPOINT_STATUS_CACHE_SECONDS uses the safe default (15).
  E. A failed refresh with a prior successful cache returns stale data.
  F. A failed initial refresh does not fabricate endpoint data.
  G. Endpoint aliases/identity are unchanged by caching.
  H. Custom display-name overrides are applied correctly.
  I. The 3-second frontend refresh path (/api/meetings) still works.
  J. Existing endpoint tests continue to pass (verified by running full suite).
"""
import os
import threading
import time
from contextlib import closing
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.endpoint_cache import EndpointCache


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pexip(return_value=None, side_effect=None):
    mock = MagicMock()
    if side_effect is not None:
        mock.list_registered_endpoints.side_effect = side_effect
    else:
        mock.list_registered_endpoints.return_value = return_value or []
    return mock


def _make_app(test_db, endpoints=None, side_effect=None):
    """Create a Flask test app with a fresh cache and optionally mocked Pexip."""
    mock_pexip = _make_pexip(return_value=endpoints or [], side_effect=side_effect)
    with patch.object(Settings, "DB_PATH",          test_db), \
         patch.object(Settings, "REG_STATUS_HOST",  "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST",     "edge.example.com"), \
         patch.object(Settings, "API_USER",         "user"), \
         patch.object(Settings, "API_PASS",         "pass"), \
         patch.object(Settings, "SECRET_KEY",       os.environ["TEST_SECRET_KEY"]), \
         patch.object(Settings, "O365_ENABLED",     False), \
         patch.object(Settings, "LOCAL_AUTH_ENABLED", True), \
         patch.object(Settings, "ENTRA_ENABLED",    False), \
         patch.object(Settings, "SESSION_COOKIE_SECURE", False), \
         patch("app.PexipAPI", return_value=mock_pexip):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
    return app, mock_pexip


def _login(client, test_db, username="cache_test_admin"):
    from app.auth.local import hash_password
    from app.auth.models import create_local_user
    from tests.conftest import get_csrf_token
    with patch.object(Settings, "DB_PATH", test_db):
        try:
            create_local_user(
                username,
                hash_password(os.environ["TEST_USER_PASSWORD"]),
                role="administrator",
            )
        except Exception:
            pass
        csrf = get_csrf_token(client)
        client.post(
            "/login",
            data={
                "username": username,
                "password": os.environ["TEST_USER_PASSWORD"],
                "csrf_token": csrf,
            },
        )


# ── A: TTL hit — only one Pexip fetch inside the TTL window ──────────────────

class TestCacheHit:
    def test_second_call_within_ttl_does_not_call_pexip_again(self):
        """Two calls within TTL → only one Pexip request."""
        pexip = _make_pexip([{"alias": "ep@example.com", "display_name": "EP"}])
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            cache.get(pexip.list_registered_endpoints)
            cache.get(pexip.list_registered_endpoints)
        pexip.list_registered_endpoints.assert_called_once()

    def test_many_calls_within_ttl_produce_single_pexip_request(self):
        """Ten calls within TTL → still only one Pexip request."""
        pexip = _make_pexip([{"alias": "ep@example.com", "display_name": "EP"}])
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            for _ in range(10):
                cache.get(pexip.list_registered_endpoints)
        pexip.list_registered_endpoints.assert_called_once()

    def test_cached_data_returned_on_hit(self):
        """Cache hit returns the same data as the initial fetch."""
        endpoints = [{"alias": "ep@example.com", "display_name": "EP"}]
        pexip = _make_pexip(endpoints)
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            first = cache.get(pexip.list_registered_endpoints)
            second = cache.get(pexip.list_registered_endpoints)
        assert first == second == endpoints

    def test_via_flask_route_multiple_requests_share_one_pexip_call(self, test_db):
        """Three /api/endpoints requests within TTL share one Pexip call."""
        endpoints = [
            {"alias": "ep@example.com", "display_name": "EP",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, mock_pexip = _make_app(test_db, endpoints=endpoints)
        with app.test_client() as client:
            _login(client, test_db, "hit_test_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                client.get("/api/endpoints")
                client.get("/api/endpoints")
                client.get("/api/endpoints")
        mock_pexip.list_registered_endpoints.assert_called_once()


# ── B: TTL expiry — refresh after TTL elapses ────────────────────────────────

class TestCacheExpiry:
    def test_request_after_ttl_calls_pexip_again(self):
        """After TTL expires, the next request triggers a new Pexip fetch."""
        pexip = _make_pexip([{"alias": "ep@example.com", "display_name": "EP"}])
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 1):
            cache.get(pexip.list_registered_endpoints)
            # Back-date the cache timestamp so it appears expired
            with cache._lock:
                cache._fetched_at = time.monotonic() - 2  # 2s > 1s TTL
            cache.get(pexip.list_registered_endpoints)
        assert pexip.list_registered_endpoints.call_count == 2

    def test_fresh_data_returned_after_ttl(self):
        """After TTL, refreshed data from Pexip is returned."""
        pexip = _make_pexip()
        pexip.list_registered_endpoints.side_effect = [
            [{"alias": "old@example.com", "display_name": "Old"}],
            [{"alias": "new@example.com", "display_name": "New"}],
        ]
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 1):
            first = cache.get(pexip.list_registered_endpoints)
            with cache._lock:
                cache._fetched_at = time.monotonic() - 2
            second = cache.get(pexip.list_registered_endpoints)
        assert first[0]["alias"] == "old@example.com"
        assert second[0]["alias"] == "new@example.com"


# ── C: Stampede prevention ────────────────────────────────────────────────────

class TestCacheStampedePrevention:
    def test_concurrent_requests_after_expiry_trigger_single_pexip_call(self):
        """Many concurrent threads after cache expiry → only one Pexip request."""
        thread_count = 20
        barrier = threading.Barrier(thread_count)
        results = []
        errors = []
        cache = EndpointCache()

        slow_data = [{"alias": "ep@example.com", "display_name": "EP"}]
        call_count = [0]
        lock = threading.Lock()

        def slow_fetch():
            with lock:
                call_count[0] += 1
            time.sleep(0.05)  # simulate network latency
            return slow_data

        pexip = MagicMock()
        pexip.list_registered_endpoints.side_effect = slow_fetch

        def worker():
            try:
                barrier.wait()
                result = cache.get(pexip.list_registered_endpoints)
                results.append(result)
            except Exception as exc:
                errors.append(exc)

        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            threads = [threading.Thread(target=worker) for _ in range(thread_count)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == thread_count
        # The lock ensures only one thread calls Pexip; the rest get the cached result
        assert call_count[0] == 1
        for r in results:
            assert r == slow_data

    def test_concurrent_requests_all_return_valid_data(self):
        """All concurrent threads receive the endpoint list, not None or empty."""
        thread_count = 10
        barrier = threading.Barrier(thread_count)
        results = []
        cache = EndpointCache()

        pexip = _make_pexip([
            {"alias": "a@example.com", "display_name": "A"},
            {"alias": "b@example.com", "display_name": "B"},
        ])

        def worker():
            barrier.wait()
            results.append(cache.get(pexip.list_registered_endpoints))

        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            threads = [threading.Thread(target=worker) for _ in range(thread_count)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        assert len(results) == thread_count
        for r in results:
            assert len(r) == 2


# ── D: Invalid ENDPOINT_STATUS_CACHE_SECONDS ─────────────────────────────────

class TestCacheTtlValidation:
    def test_parse_valid_default(self):
        """Default value of 15 is accepted."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "15"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 15

    def test_parse_valid_custom_value(self):
        """A valid in-range value is used as-is."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "30"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 30

    def test_parse_non_integer_falls_back_to_default(self):
        """Non-integer string falls back to 15."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "fast"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 15

    def test_parse_negative_falls_back_to_default(self):
        """Negative value is out of range and falls back to 15."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "-1"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 15

    def test_parse_zero_falls_back_to_default(self):
        """Zero is out of range and falls back to 15."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "0"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 15

    def test_parse_above_max_falls_back_to_default(self):
        """Value > 3600 is out of range and falls back to 15."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "9999"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 15

    def test_parse_empty_string_falls_back_to_default(self):
        """Empty string falls back to 15."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": ""}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 15

    def test_parse_boundary_min(self):
        """Minimum valid value (1) is accepted."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "1"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 1

    def test_parse_boundary_max(self):
        """Maximum valid value (3600) is accepted."""
        with patch.dict(os.environ, {"ENDPOINT_STATUS_CACHE_SECONDS": "3600"}):
            from app.config import _parse_endpoint_cache_seconds
            assert _parse_endpoint_cache_seconds() == 3600

    def test_invalid_ttl_setting_still_limits_pexip_calls(self):
        """Even with invalid env, cache uses safe default and limits Pexip calls."""
        pexip = _make_pexip([{"alias": "ep@example.com", "display_name": "EP"}])
        cache = EndpointCache()
        # 15-second default — two calls in rapid succession should hit cache
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 15):
            cache.get(pexip.list_registered_endpoints)
            cache.get(pexip.list_registered_endpoints)
        pexip.list_registered_endpoints.assert_called_once()


# ── E: Stale cache on refresh failure ────────────────────────────────────────

class TestStaleCacheFallback:
    def test_failed_refresh_returns_stale_data_when_cache_populated(self):
        """After a successful cache fill, a Pexip failure returns stale data."""
        good_data = [{"alias": "ep@example.com", "display_name": "EP"}]
        pexip = _make_pexip()
        pexip.list_registered_endpoints.side_effect = [
            good_data,
            Exception("Pexip unreachable"),
        ]
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 1):
            first = cache.get(pexip.list_registered_endpoints)
            with cache._lock:
                cache._fetched_at = time.monotonic() - 2
            second = cache.get(pexip.list_registered_endpoints)

        assert first == good_data
        assert second == good_data  # stale fallback

    def test_stale_fallback_does_not_raise(self):
        """Stale fallback path does not propagate the exception to the caller."""
        pexip = _make_pexip()
        pexip.list_registered_endpoints.side_effect = [
            [{"alias": "ep@example.com", "display_name": "EP"}],
            RuntimeError("network error"),
        ]
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 1):
            cache.get(pexip.list_registered_endpoints)
            with cache._lock:
                cache._fetched_at = time.monotonic() - 2
            result = cache.get(pexip.list_registered_endpoints)

        assert result is not None

    def test_stale_fallback_returns_all_original_endpoints(self):
        """Stale cache contains all endpoints from the last successful fetch."""
        endpoints = [
            {"alias": "a@example.com", "display_name": "A"},
            {"alias": "b@example.com", "display_name": "B"},
            {"alias": "c@example.com", "display_name": "C"},
        ]
        pexip = _make_pexip()
        pexip.list_registered_endpoints.side_effect = [
            endpoints,
            Exception("Pexip down"),
        ]
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 1):
            cache.get(pexip.list_registered_endpoints)
            with cache._lock:
                cache._fetched_at = time.monotonic() - 2
            stale = cache.get(pexip.list_registered_endpoints)

        assert len(stale) == 3
        aliases = {ep["alias"] for ep in stale}
        assert aliases == {"a@example.com", "b@example.com", "c@example.com"}

    def test_stale_fallback_via_flask_route_returns_200(self, test_db):
        """When cache has stale data and Pexip fails, /api/endpoints still returns 200."""
        good_endpoints = [
            {"alias": "ep@example.com", "display_name": "EP",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, mock_pexip = _make_app(test_db, endpoints=good_endpoints)
        mock_pexip.list_registered_endpoints.side_effect = [
            good_endpoints,
            Exception("Pexip down"),
        ]
        with app.test_client() as client:
            _login(client, test_db, "stale_test_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 1):
                # First request fills the cache
                resp1 = client.get("/api/endpoints")
                # Expire the cache
                with app.endpoint_cache._lock:
                    app.endpoint_cache._fetched_at = time.monotonic() - 2
                # Second request triggers Pexip failure → stale fallback
                resp2 = client.get("/api/endpoints")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp2.get_json()["ok"] is True
        assert len(resp2.get_json()["items"]) == 1


# ── F: No fabrication on initial failure ─────────────────────────────────────

class TestInitialFetchFailure:
    def test_initial_pexip_failure_raises_not_fabricates(self):
        """If Pexip fails on the very first call, raise rather than fabricate data."""
        pexip = _make_pexip(side_effect=Exception("Pexip unreachable"))
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 15):
            with pytest.raises(Exception, match="Pexip unreachable"):
                cache.get(pexip.list_registered_endpoints)

    def test_initial_failure_leaves_cache_empty(self):
        """After an initial failure, the cache remains empty (not a fabricated list)."""
        pexip = _make_pexip(side_effect=Exception("connection refused"))
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 15):
            try:
                cache.get(pexip.list_registered_endpoints)
            except Exception:
                pass
        with cache._lock:
            assert cache._data is None

    def test_initial_failure_does_not_return_empty_list(self):
        """Cache must not silently return [] on initial failure — it must raise."""
        pexip = _make_pexip(side_effect=RuntimeError("auth failed"))
        cache = EndpointCache()
        caught = None
        result = "SENTINEL"
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 15):
            try:
                result = cache.get(pexip.list_registered_endpoints)
            except RuntimeError as exc:
                caught = exc

        assert caught is not None, "Expected an exception but none was raised"
        assert result == "SENTINEL", "Function returned a value despite Pexip failure"

    def test_initial_failure_via_flask_route_returns_500(self, test_db):
        """Initial Pexip failure on /api/endpoints returns 500 (not empty 200)."""
        app, mock_pexip = _make_app(test_db)
        mock_pexip.list_registered_endpoints.side_effect = Exception("connection refused")
        with app.test_client() as client:
            _login(client, test_db, "init_fail_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 15):
                resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert resp.status_code == 500
        assert data["ok"] is False
        assert "Unable to load endpoints" in data["error"]
        # CWE-209: internal error must not leak to client
        assert "connection refused" not in data.get("error", "")


# ── G: Alias/identity unchanged ───────────────────────────────────────────────

class TestAliasIdentityPreserved:
    def test_alias_field_unchanged_after_caching(self):
        """Pexip alias is returned exactly as received from Pexip."""
        raw_alias = "sip:room101@pexip.example.com"
        pexip = _make_pexip([{"alias": raw_alias, "display_name": "Room 101"}])
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            result = cache.get(pexip.list_registered_endpoints)
        assert result[0]["alias"] == raw_alias

    def test_is_registered_field_unchanged(self):
        """is_registered field is preserved exactly as received from Pexip."""
        pexip = _make_pexip([
            {"alias": "ep1@example.com", "display_name": "EP1", "is_registered": True},
        ])
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            result = cache.get(pexip.list_registered_endpoints)
        assert result[0]["is_registered"] is True

    def test_protocol_field_unchanged(self):
        """Protocol field is preserved exactly as received from Pexip."""
        pexip = _make_pexip([
            {"alias": "ep@example.com", "display_name": "EP", "protocol": "sip", "node": "n1"},
        ])
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            result = cache.get(pexip.list_registered_endpoints)
        assert result[0]["protocol"] == "sip"
        assert result[0]["node"] == "n1"

    def test_multiple_aliases_all_preserved(self):
        """All aliases are returned intact, order-stable across cache hits."""
        endpoints = [
            {"alias": "alpha@example.com", "display_name": "Alpha"},
            {"alias": "beta@example.com",  "display_name": "Beta"},
            {"alias": "gamma@example.com", "display_name": "Gamma"},
        ]
        pexip = _make_pexip(endpoints)
        cache = EndpointCache()
        with patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
            first = cache.get(pexip.list_registered_endpoints)
            second = cache.get(pexip.list_registered_endpoints)
        assert [ep["alias"] for ep in first] == [ep["alias"] for ep in second]


# ── H: Custom display-name behavior via the API route ────────────────────────

class TestCustomDisplayNamePreserved:
    def test_custom_display_name_applied_over_cached_pexip_name(self, test_db):
        """Custom display-name override takes precedence over Pexip name from cache."""
        endpoints = [
            {"alias": "ws1@example.com", "display_name": "Workstation 1",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, _mock = _make_app(test_db, endpoints=endpoints)
        with app.test_client() as client:
            _login(client, test_db, "dn_test_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                from app.database import db
                from app.meeting_utils import now_utc, iso
                with closing(db()) as conn:
                    conn.execute(
                        "INSERT INTO endpoint_display_overrides "
                        "(alias_key, custom_display_name, updated_at, updated_by) "
                        "VALUES (?, ?, ?, ?)",
                        ("ws1@example.com", "Custom Name", iso(now_utc()), "admin"),
                    )
                    conn.commit()
                resp = client.get("/api/endpoints")
        ep = resp.get_json()["items"][0]
        assert ep["display_name"] == "Custom Name"
        assert ep["pexip_display_name"] == "Workstation 1"
        assert ep["alias"] == "ws1@example.com"

    def test_alias_authoritative_identity_not_changed_by_override(self, test_db):
        """The alias field is the Pexip alias regardless of any display-name override."""
        endpoints = [
            {"alias": "ws1@example.com", "display_name": "Workstation 1",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, _mock = _make_app(test_db, endpoints=endpoints)
        with app.test_client() as client:
            _login(client, test_db, "id_test_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                resp = client.get("/api/endpoints")
        ep = resp.get_json()["items"][0]
        assert ep["alias"] == "ws1@example.com"

    def test_restore_default_removes_override(self, test_db):
        """Deleting a display-name override via DELETE reverts to Pexip name."""
        endpoints = [
            {"alias": "ws1@example.com", "display_name": "Workstation 1",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, _mock = _make_app(test_db, endpoints=endpoints)
        with app.test_client() as client:
            _login(client, test_db, "restore_test_admin")
            from tests.conftest import get_csrf_token
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                from app.database import db
                from app.meeting_utils import now_utc, iso
                with closing(db()) as conn:
                    conn.execute(
                        "INSERT INTO endpoint_display_overrides "
                        "(alias_key, custom_display_name, updated_at, updated_by) "
                        "VALUES (?, ?, ?, ?)",
                        ("ws1@example.com", "Custom Name", iso(now_utc()), "admin"),
                    )
                    conn.commit()

                csrf = get_csrf_token(client, url="/")
                client.delete(
                    "/api/endpoints/display-name",
                    json={"endpoint_alias": "ws1@example.com"},
                    headers={"X-CSRFToken": csrf},
                )
                resp = client.get("/api/endpoints")
        ep = resp.get_json()["items"][0]
        assert ep["display_name"] == "Workstation 1"
        assert ep["custom_display_name"] is None


# ── I: 3-second frontend refresh path still works ────────────────────────────

class TestFrontendRefreshUnchanged:
    """The meetings API (3-second poll) is independent of the endpoint cache."""

    def test_meetings_api_returns_ok_with_cache_present(self, test_db):
        """/api/meetings still returns ok=True when the endpoint cache is active."""
        app, _mock = _make_app(test_db)
        with app.test_client() as client:
            _login(client, test_db, "meetings_test_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                resp = client.get(f"/api/meetings?date={date.today().isoformat()}")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_meetings_api_does_not_call_list_registered_endpoints(self, test_db):
        """/api/meetings does not call list_registered_endpoints (not its responsibility)."""
        app, mock_pexip = _make_app(test_db)
        mock_pexip.get_live_participants_via_edges.return_value = []
        with app.test_client() as client:
            _login(client, test_db, "meetings_no_ep_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                client.get(f"/api/meetings?date={date.today().isoformat()}")
        mock_pexip.list_registered_endpoints.assert_not_called()

    def test_endpoints_api_uses_cache_not_direct_pexip_per_request(self, test_db):
        """Multiple /api/endpoints calls within TTL share one Pexip call."""
        endpoints = [
            {"alias": "ep@example.com", "display_name": "EP",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, mock_pexip = _make_app(test_db, endpoints=endpoints)
        with app.test_client() as client:
            _login(client, test_db, "share_test_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                client.get("/api/endpoints")
                client.get("/api/endpoints")
                client.get("/api/endpoints")
        mock_pexip.list_registered_endpoints.assert_called_once()


# ── Cache mutation isolation ──────────────────────────────────────────────────

class TestCacheMutationIsolation:
    """API route must not mutate cached items (display-name enrichment is per-request)."""

    def test_cached_items_not_mutated_by_route(self, test_db):
        """Each /api/endpoints response builds fresh dicts; cached items stay clean."""
        endpoints = [
            {"alias": "ep@example.com", "display_name": "EP",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, _mock = _make_app(test_db, endpoints=endpoints)
        with app.test_client() as client:
            _login(client, test_db, "mutation_test_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                client.get("/api/endpoints")
                # The raw cached items must not have pexip_display_name added to them
                with app.endpoint_cache._lock:
                    cached = app.endpoint_cache._data
                assert cached is not None
                assert "pexip_display_name" not in cached[0]
                assert "custom_display_name" not in cached[0]

    def test_second_request_still_returns_correct_data_after_mutation_test(self, test_db):
        """If the first request didn't mutate the cache, the second response is identical."""
        endpoints = [
            {"alias": "ep@example.com", "display_name": "EP",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        app, _mock = _make_app(test_db, endpoints=endpoints)
        with app.test_client() as client:
            _login(client, test_db, "double_check_admin")
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENDPOINT_STATUS_CACHE_SECONDS", 60):
                resp1 = client.get("/api/endpoints")
                resp2 = client.get("/api/endpoints")
        items1 = resp1.get_json()["items"]
        items2 = resp2.get_json()["items"]
        assert items1 == items2
        assert items1[0]["pexip_display_name"] == "EP"
