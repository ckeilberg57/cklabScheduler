"""
Tests for the POST /api/meetings/<id>/edit endpoint.

Covers:
  - Scheduled meeting: full edit (title, start, end, endpoints, notes)
  - Active meeting: end_time + notes only
  - Status gates: ending/ended/ended_with_errors reject edits
  - Time validation: end <= start rejected; past end_time for active rejected
  - Auth + CSRF enforcement
  - 404 for non-existent meeting
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
from tests.conftest import get_csrf_token, insert_meeting, insert_endpoint


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
            data={"username": "testuser", "password": os.environ["TEST_USER_PASSWORD"], "csrf_token": csrf},
            follow_redirects=True,
        )


def post_edit(client, meeting_id, payload, *, csrf=None, test_db=None):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRFToken"] = csrf
    with (patch.object(Settings, "DB_PATH", test_db) if test_db else __import__('contextlib').nullcontext()):
        return client.post(
            f"/api/meetings/{meeting_id}/edit",
            data=json.dumps(payload),
            headers=headers,
        )


class TestMeetingEditAuth:
    def test_requires_login_or_csrf(self, test_db):
        """Unauthenticated POST with no CSRF token returns 400 (CSRF) or 401/302."""
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.post(
                "/api/meetings/1/edit",
                data=json.dumps({}),
                content_type="application/json",
            )
            # CSRF fires before @login_required, so 400 is the expected response
            assert resp.status_code in (400, 401, 302)

    def test_requires_csrf_when_authenticated(self, test_db):
        """Authenticated POST without X-CSRFToken header is rejected (400)."""
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
            # POST without X-CSRFToken header
            resp = client.post(
                f"/api/meetings/{mid}/edit",
                data=json.dumps({
                    "start_time": iso(now + timedelta(hours=1)),
                    "end_time": iso(now + timedelta(hours=2)),
                }),
                content_type="application/json",
            )
            assert resp.status_code in (400, 403)


class TestScheduledMeetingEdit:
    def _setup(self, test_db, **meeting_kwargs):
        now = now_utc()
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                defaults = {
                    "start_time": iso(now + timedelta(hours=1)),
                    "end_time": iso(now + timedelta(hours=2)),
                }
                defaults.update(meeting_kwargs)
                mid = insert_meeting(conn, **defaults)
        return mid, now

    def test_edit_title_and_times(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        mid, now = self._setup(test_db, title="Original")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                new_start = iso(now + timedelta(hours=3))
                new_end = iso(now + timedelta(hours=4))
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({"title": "Updated", "start_time": new_start, "end_time": new_end, "notes": "n"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["item"]["title"] == "Updated"

    def test_end_before_start_rejected(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        mid, now = self._setup(test_db)
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
            data = resp.get_json()
            assert "end_time" in data["error"].lower()

    def test_missing_times_rejected(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        mid, _ = self._setup(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({"notes": "only notes"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400

    def test_edit_notes(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        mid, now = self._setup(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({
                        "start_time": iso(now + timedelta(hours=1)),
                        "end_time": iso(now + timedelta(hours=2)),
                        "notes": "updated notes",
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 200
            assert resp.get_json()["item"]["notes"] == "updated notes"

    def test_not_found_returns_404(self, test_db):
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


class TestActiveMeetingEdit:
    def test_active_meeting_can_update_end_time(self, test_db):
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="started",
                    started_at=iso(now - timedelta(minutes=10)),
                    start_time=iso(now - timedelta(minutes=10)),
                    end_time=iso(now + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                new_end = iso(now + timedelta(hours=2))
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({"end_time": new_end, "notes": ""}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True

    def test_active_meeting_past_end_time_rejected(self, test_db):
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="started",
                    started_at=iso(now - timedelta(minutes=10)),
                    start_time=iso(now - timedelta(minutes=10)),
                    end_time=iso(now + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                past_end = iso(now - timedelta(hours=1))
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({"end_time": past_end}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400

    def test_started_with_errors_can_update_end_time(self, test_db):
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="started_with_errors",
                    started_at=iso(now - timedelta(minutes=5)),
                    start_time=iso(now - timedelta(minutes=5)),
                    end_time=iso(now + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                new_end = iso(now + timedelta(hours=3))
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({"end_time": new_end, "notes": "fixed"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 200


class TestEditStatusGates:
    @pytest.mark.parametrize("status", ["ending", "ended", "ended_with_errors", "starting"])
    def test_uneditable_statuses_rejected(self, test_db, status):
        app = make_app(test_db)
        now = now_utc()
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status=status,
                    start_time=iso(now - timedelta(hours=2)),
                    end_time=iso(now - timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({
                        "start_time": iso(now + timedelta(hours=1)),
                        "end_time": iso(now + timedelta(hours=2)),
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["ok"] is False
            assert status in data["error"]
