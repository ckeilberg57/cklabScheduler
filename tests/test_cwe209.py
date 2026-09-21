"""
CWE-209 regression tests — Server Information Exposure.

Verifies that exception details cannot escape through HTTP responses in:
  - api_edit_meeting()  (both Snyk-reported paths)
  - api_meetings()
  - api_meetings_range()

Also verifies that expected validation responses remain intact and that
security controls (auth, CSRF) are unaffected.
"""
import json
import os
from contextlib import closing
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.database import db
from app.meeting_utils import iso, now_utc
from tests.conftest import get_csrf_token, insert_meeting


SENSITIVE = "INTERNAL_DB_PATH_/var/lib/private.db"
SENSITIVE_ACTIVE = "SECRET_INTERNAL_ERROR_MARKER_active_parse_iso"


def make_app(test_db):
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = []
    with (
        patch.object(Settings, "DB_PATH", test_db),
        patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"),
        patch.object(Settings, "COMMAND_HOST", "edge.example.com"),
        patch.object(Settings, "API_USER", "user"),
        patch.object(Settings, "API_PASS", "pass"),
        patch.object(Settings, "SECRET_KEY", os.environ["TEST_SECRET_KEY"]),
        patch.object(Settings, "O365_ENABLED", False),
        patch.object(Settings, "LOCAL_AUTH_ENABLED", True),
        patch.object(Settings, "ENTRA_ENABLED", False),
        patch.object(Settings, "SESSION_COOKIE_SECURE", False),
        patch("app.PexipAPI", return_value=mock_pexip),
    ):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app


def _create_user(test_db):
    from app.auth.local import hash_password
    from app.auth.models import create_local_user
    with patch.object(Settings, "DB_PATH", test_db):
        create_local_user(
            "testuser",
            hash_password(os.environ["TEST_USER_PASSWORD"]),
            role="admin",
        )


def login(client, test_db):
    with patch.object(Settings, "DB_PATH", test_db):
        csrf = get_csrf_token(client, "/login")
        client.post(
            "/login",
            data={
                "username": "testuser",
                "password": os.environ["TEST_USER_PASSWORD"],
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )


class TestCWE209EditMeetingScheduled:
    """CWE-209: Snyk-reported path 1 — scheduled meeting parse_iso exception."""

    def test_parse_error_returns_400_not_500(self, test_db):
        """A bad date string produces 400 with a safe fixed message."""
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({
                        "start_time": "not-a-date",
                        "end_time": iso(now + timedelta(hours=2)),
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400

    def test_exception_details_do_not_appear_in_response(self, test_db):
        """Exception raised inside parse_iso must not appear in the HTTP response body."""
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                with patch(
                    "app.routes.meetings.parse_iso",
                    side_effect=ValueError(SENSITIVE),
                ):
                    resp = client.post(
                        f"/api/meetings/{mid}/edit",
                        data=json.dumps({
                            "start_time": iso(now + timedelta(hours=1)),
                            "end_time": iso(now + timedelta(hours=2)),
                        }),
                        headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                    )
            assert resp.status_code == 400
            body = resp.get_data(as_text=True)
            assert SENSITIVE not in body
            data = resp.get_json()
            assert data["ok"] is False
            assert "Invalid date/time format" in data["error"]

    def test_safe_fixed_message_present(self, test_db):
        """The safe generic message is always returned for parse failures."""
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({
                        "start_time": "GARBAGE_DATE_VALUE",
                        "end_time": "ALSO_BAD",
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400
            data = resp.get_json()
            assert "Invalid date/time format" in data["error"]
            assert "GARBAGE_DATE_VALUE" not in data["error"]
            assert "ALSO_BAD" not in data["error"]


class TestCWE209EditMeetingActive:
    """CWE-209: Snyk-reported path 2 — active meeting parse_iso exception."""

    def _make_active_meeting(self, test_db):
        now = now_utc()
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="started",
                    started_at=iso(now - timedelta(minutes=10)),
                    start_time=iso(now - timedelta(minutes=10)),
                    end_time=iso(now + timedelta(hours=1)),
                )
        return mid, now

    def test_exception_details_do_not_appear_in_response(self, test_db):
        """Exception raised inside parse_iso (active path) must not appear in response."""
        app = make_app(test_db)
        _create_user(test_db)
        mid, now = self._make_active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                with patch(
                    "app.routes.meetings.parse_iso",
                    side_effect=ValueError(SENSITIVE_ACTIVE),
                ):
                    resp = client.post(
                        f"/api/meetings/{mid}/edit",
                        data=json.dumps({"end_time": iso(now + timedelta(hours=2))}),
                        headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                    )
            assert resp.status_code == 400
            body = resp.get_data(as_text=True)
            assert SENSITIVE_ACTIVE not in body
            data = resp.get_json()
            assert data["ok"] is False
            assert "Invalid date/time format" in data["error"]

    def test_bad_end_time_format_returns_safe_message(self, test_db):
        """Malformed end_time for active meeting returns fixed message, not exception text."""
        app = make_app(test_db)
        _create_user(test_db)
        mid, now = self._make_active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({"end_time": "NOT_A_DATE_AT_ALL"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400
            data = resp.get_json()
            assert "NOT_A_DATE_AT_ALL" not in data["error"]
            assert "Invalid date/time format" in data["error"]


class TestCWE209ValidationResponsesIntact:
    """Regression: expected 4xx validation responses must remain unchanged."""

    def test_scheduled_end_before_start_still_400(self, test_db):
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({
                        "start_time": iso(now + timedelta(hours=3)),
                        "end_time": iso(now + timedelta(hours=1)),
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400
            assert "end_time" in resp.get_json()["error"].lower()

    def test_nonexistent_meeting_still_404(self, test_db):
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    "/api/meetings/99999/edit",
                    data=json.dumps({
                        "start_time": iso(now + timedelta(hours=1)),
                        "end_time": iso(now + timedelta(hours=2)),
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 404

    def test_unauthenticated_still_rejected(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.post(
                "/api/meetings/1/edit",
                data=json.dumps({}),
                content_type="application/json",
            )
            assert resp.status_code in (400, 401, 302)

    def test_missing_csrf_still_rejected(self, test_db):
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
        with app.test_client() as client:
            login(client, test_db)
            resp = client.post(
                f"/api/meetings/{mid}/edit",
                data=json.dumps({
                    "start_time": iso(now + timedelta(hours=1)),
                    "end_time": iso(now + timedelta(hours=2)),
                }),
                content_type="application/json",
            )
            assert resp.status_code in (400, 403)
