import os
import re
import secrets

import pytest
from contextlib import closing
from unittest.mock import patch

from app.config import Settings
from app.database import db, init_db
from app.meeting_utils import iso, now_utc
from datetime import timedelta


def _ensure_test_environment():
    """Generate synthetic test credentials at pytest startup.

    Uses os.environ.setdefault so CI-supplied values are never overwritten.
    Values are cryptographically random and have no meaning outside the test run.
    """
    os.environ.setdefault("TEST_USER_PASSWORD", secrets.token_urlsafe(32))
    os.environ.setdefault("TEST_SECRET_KEY",    secrets.token_hex(32))


_ensure_test_environment()

# ── CSRF helpers ──────────────────────────────────────────────────────────────
_CSRF_META_RE  = re.compile(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"')
_CSRF_INPUT_RE = re.compile(r'<input[^>]+name="csrf_token"[^>]+value="([^"]+)"', re.IGNORECASE)


def get_csrf_token(client, url="/login"):
    """Return a CSRF token valid for *client*'s current session.

    Makes a GET request to *url* and extracts the CSRF token from the
    rendered HTML.  The Flask test client's session cookie ensures the
    token is tied to this client's session, so it passes Flask-WTF
    validation on the subsequent POST.

    Use url="/login" (default) when not yet authenticated — the login
    form renders the token in a hidden ``<input name="csrf_token">`` field.
    Use url="/" when already authenticated — the main page renders the
    token in ``<meta name="csrf-token">``.
    """
    resp = client.get(url)
    html = resp.get_data(as_text=True)
    m = _CSRF_META_RE.search(html) or _CSRF_INPUT_RE.search(html)
    if not m:
        raise RuntimeError(
            f"No CSRF token found in GET {url!r} (status {resp.status_code})"
        )
    return m.group(1)


@pytest.fixture
def test_db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def test_db(test_db_path):
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        yield test_db_path


def insert_meeting(conn, **kwargs):
    defaults = {
        "title": "Test Meeting",
        "meeting_alias": "doctest1234567890",
        "start_time": iso(now_utc() - timedelta(minutes=5)),
        "end_time": iso(now_utc() + timedelta(hours=1)),
        "status": "scheduled",
        "started_at": None,
        "ended_at": None,
        "created_at": iso(now_utc()),
        "updated_at": iso(now_utc()),
        "notes": "",
    }
    defaults.update(kwargs)
    cur = conn.execute(
        """
        INSERT INTO meetings
            (title, meeting_alias, start_time, end_time, status,
             started_at, ended_at, created_at, updated_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            defaults["title"], defaults["meeting_alias"],
            defaults["start_time"], defaults["end_time"], defaults["status"],
            defaults["started_at"], defaults["ended_at"],
            defaults["created_at"], defaults["updated_at"], defaults["notes"],
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_endpoint(conn, meeting_id, **kwargs):
    defaults = {
        "endpoint_alias": "ep@example.com",
        "display_name": "Test Endpoint",
        "role": "host",
        "status": "scheduled",
    }
    defaults.update(kwargs)
    cur = conn.execute(
        """
        INSERT INTO meeting_endpoints (meeting_id, endpoint_alias, display_name, role, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (meeting_id, defaults["endpoint_alias"], defaults["display_name"],
         defaults["role"], defaults["status"]),
    )
    conn.commit()
    return cur.lastrowid
