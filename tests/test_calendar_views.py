"""
Tests for the /api/meetings/range endpoint (calendar month view backing).
"""
import os
from contextlib import closing
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.database import db
from app.meeting_utils import iso, now_utc
from tests.conftest import get_csrf_token, insert_meeting


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


class TestMeetingsRange:
    def test_requires_auth(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/meetings/range?start=2025-01-01&end=2025-01-31")
            assert resp.status_code in (401, 302)

    def test_missing_params_returns_400(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range")
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["ok"] is False

    def test_missing_end_returns_400(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2025-01-01")
            assert resp.status_code == 400

    def test_end_before_start_returns_400(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2025-02-01&end=2025-01-01")
            assert resp.status_code == 400
            data = resp.get_json()
            assert "end must be" in data["error"].lower()

    def test_range_over_one_year_returns_400(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2024-01-01&end=2026-01-01")
            assert resp.status_code == 400
            data = resp.get_json()
            assert "year" in data["error"].lower() or "366" in data["error"]

    def test_invalid_date_format_returns_400(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=not-a-date&end=2025-01-31")
            assert resp.status_code == 400

    def test_returns_empty_list_when_no_meetings(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2025-01-01&end=2025-01-31")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert data["items"] == []

    def test_returns_meetings_in_range(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    title="January Meeting",
                    start_time="2025-01-15T14:00:00+00:00",
                    end_time="2025-01-15T15:00:00+00:00",
                )
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2025-01-01&end=2025-01-31")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True
            assert len(data["items"]) == 1
            assert data["items"][0]["title"] == "January Meeting"

    def test_excludes_meetings_outside_range(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    title="February Meeting",
                    start_time="2025-02-10T14:00:00+00:00",
                    end_time="2025-02-10T15:00:00+00:00",
                )
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2025-01-01&end=2025-01-31")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["items"] == []

    def test_meetings_spanning_month_boundary_included(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    title="Spanning Meeting",
                    start_time="2025-01-31T23:00:00+00:00",
                    end_time="2025-02-01T01:00:00+00:00",
                )
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2025-01-01&end=2025-01-31")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["items"]) == 1

    def test_response_schema_fields(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    start_time="2025-06-10T10:00:00+00:00",
                    end_time="2025-06-10T11:00:00+00:00",
                )
        with app.test_client() as client:
            login(client, test_db)
            resp = client.get("/api/meetings/range?start=2025-06-01&end=2025-06-30")
            data = resp.get_json()
            item = data["items"][0]
            for field in ("id", "title", "start_time", "end_time", "status", "endpoints"):
                assert field in item, f"Missing field: {field}"
