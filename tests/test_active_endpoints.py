"""
Tests for active-meeting endpoint add/remove (Features 1.1-1.14).
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


def make_app(test_db, mock_pexip=None):
    if mock_pexip is None:
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


def _active_meeting(test_db, status="started"):
    now = now_utc()
    with patch.object(Settings, "DB_PATH", test_db):
        with closing(db()) as conn:
            mid = insert_meeting(
                conn,
                status=status,
                started_at=iso(now - timedelta(minutes=5)),
                start_time=iso(now - timedelta(minutes=5)),
                end_time=iso(now + timedelta(hours=1)),
            )
    return mid


def _post_json(client, url, payload, csrf):
    return client.post(
        url,
        data=json.dumps(payload),
        headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
    )


class TestActiveEndpointAddRemovePermissions:
    def test_active_meeting_permits_endpoint_add(self, test_db):
        """Test 1: Active meeting accepts endpoint add."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.dial_endpoint_to_meeting.return_value = {"result": {}}
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/add_endpoint",
                                  {"endpoint_alias": "ep@example.com", "display_name": "Ep", "role": "host"}, csrf)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True

    def test_active_meeting_rejects_subject_modification(self, test_db):
        """Test 2: Active meeting still rejects title modification via edit endpoint."""
        app = make_app(test_db)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/edit",
                                  {"title": "HACKED", "end_time": iso(now_utc() + timedelta(hours=2))}, csrf)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["item"]["title"] != "HACKED"

    def test_active_meeting_rejects_start_time_modification(self, test_db):
        """Test 3: Active meeting still rejects start_time modification."""
        app = make_app(test_db)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                now = now_utc()
                resp = _post_json(client, f"/api/meetings/{mid}/edit",
                                  {"start_time": iso(now + timedelta(hours=5)),
                                   "end_time": iso(now + timedelta(hours=6))}, csrf)
            if resp.status_code == 200:
                data = resp.get_json()
                item = data.get("item", {})
                from app.meeting_utils import parse_iso
                stored_start = parse_iso(item.get("start_time", ""))
                sent_start = parse_iso(iso(now_utc() + timedelta(hours=5)))
                diff = abs((stored_start - sent_start).total_seconds())
                assert diff > 60, "Active meeting must not allow start_time modification"

    def test_add_endpoint_does_not_redial_existing(self, test_db):
        """Test 4: Adding one endpoint does not redial existing endpoints."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.dial_endpoint_to_meeting.return_value = {"result": {}}
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        now = now_utc()
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn, status="started",
                    started_at=iso(now - timedelta(minutes=5)),
                    start_time=iso(now - timedelta(minutes=5)),
                    end_time=iso(now + timedelta(hours=1)),
                )
                insert_endpoint(conn, mid, endpoint_alias="existing@example.com", display_name="Existing")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/add_endpoint",
                                  {"endpoint_alias": "new@example.com", "display_name": "New"}, csrf)
            assert resp.status_code == 200
        assert mock_pexip.dial_endpoint_to_meeting.call_count == 1
        called_alias = mock_pexip.dial_endpoint_to_meeting.call_args[0][1]
        assert called_alias == "new@example.com"

    def test_add_endpoint_uses_existing_conference(self, test_db):
        """Test 5: Newly added endpoint uses existing conference (same meeting_alias)."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.dial_endpoint_to_meeting.return_value = {"result": {}}
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                row = conn.execute("SELECT meeting_alias FROM meetings WHERE id = ?", (mid,)).fetchone()
                expected_alias = row["meeting_alias"]
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                _post_json(client, f"/api/meetings/{mid}/add_endpoint",
                           {"endpoint_alias": "ep@example.com"}, csrf)
        called_alias = mock_pexip.dial_endpoint_to_meeting.call_args[0][0]
        assert called_alias == expected_alias

    def test_newly_added_endpoint_not_optimistically_live(self, test_db):
        """Test 6: Newly added endpoint is not optimistically set to LIVE."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.dial_endpoint_to_meeting.return_value = {"result": {}}
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/add_endpoint",
                                  {"endpoint_alias": "ep@example.com", "display_name": "Ep"}, csrf)
            data = resp.get_json()
            eps = data["item"]["endpoints"]
            ep = next(e for e in eps if e["endpoint_alias"] == "ep@example.com")
            assert ep["live"] is False, "Newly added endpoint must not be optimistically LIVE"

    def test_dial_failure_rolled_back(self, test_db):
        """Test 7: Dial failure causes DB rollback; endpoint not added."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.dial_endpoint_to_meeting.side_effect = RuntimeError("dial failed")
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/add_endpoint",
                                  {"endpoint_alias": "ep@example.com"}, csrf)
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["ok"] is False
            assert "dial" in data["error"].lower() or "failed" in data["error"].lower()
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                ep = conn.execute(
                    "SELECT id FROM meeting_endpoints WHERE meeting_id = ? AND endpoint_alias = ?",
                    (mid, "ep@example.com"),
                ).fetchone()
                assert ep is None, "Failed dial must roll back DB record"

    def test_remove_endpoint_disconnects_only_target(self, test_db):
        """Test 8: Removing endpoint disconnects only the target endpoint."""
        target_uuid = "uuid-target-001"
        other_uuid = "uuid-other-002"
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.get_live_participants.return_value = [
            {"uuid": target_uuid, "display_name": "Target", "remote_alias": "target@example.com",
             "role": "host", "remote_address": ""},
            {"uuid": other_uuid, "display_name": "Other", "remote_alias": "other@example.com",
             "role": "guest", "remote_address": ""},
        ]
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        now = now_utc()
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn, status="started",
                    started_at=iso(now - timedelta(minutes=5)),
                    start_time=iso(now - timedelta(minutes=5)),
                    end_time=iso(now + timedelta(hours=1)),
                )
                insert_endpoint(conn, mid, endpoint_alias="target@example.com", display_name="Target")
                insert_endpoint(conn, mid, endpoint_alias="other@example.com", display_name="Other")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/remove_endpoint",
                                  {"endpoint_alias": "target@example.com"}, csrf)
            assert resp.status_code == 200
        assert mock_pexip.disconnect_participant.call_count == 1
        called_uuid = mock_pexip.disconnect_participant.call_args[0][1]
        assert called_uuid == target_uuid

    def test_remove_endpoint_does_not_affect_other_endpoint(self, test_db):
        """Test 9: Removing endpoint does not disconnect another endpoint."""
        other_uuid = "uuid-other-002"
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.get_live_participants.return_value = [
            {"uuid": "uuid-target", "display_name": "Target", "remote_alias": "target@example.com",
             "role": "host", "remote_address": ""},
            {"uuid": other_uuid, "display_name": "Other", "remote_alias": "other@example.com",
             "role": "guest", "remote_address": ""},
        ]
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        now = now_utc()
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn, status="started",
                    started_at=iso(now - timedelta(minutes=5)),
                    start_time=iso(now - timedelta(minutes=5)),
                    end_time=iso(now + timedelta(hours=1)),
                )
                insert_endpoint(conn, mid, endpoint_alias="target@example.com", display_name="Target")
                insert_endpoint(conn, mid, endpoint_alias="other@example.com", display_name="Other")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                _post_json(client, f"/api/meetings/{mid}/remove_endpoint",
                           {"endpoint_alias": "target@example.com"}, csrf)
        for c in mock_pexip.disconnect_participant.call_args_list:
            assert c[0][1] != other_uuid, "Must not disconnect the other endpoint"

    def test_disconnect_all_not_used(self, test_db):
        """Test 10: disconnectAll is not called when removing an endpoint."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.get_live_participants.return_value = [
            {"uuid": "uuid-1", "display_name": "EP", "remote_alias": "ep@example.com",
             "role": "host", "remote_address": ""},
        ]
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_endpoint(conn, mid, endpoint_alias="ep@example.com")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                _post_json(client, f"/api/meetings/{mid}/remove_endpoint",
                           {"endpoint_alias": "ep@example.com"}, csrf)
        mock_pexip.disconnect_conference.assert_not_called()

    def test_ambiguous_participant_not_disconnected(self, test_db):
        """Test 11: Ambiguous participant match does not disconnect any participant."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.get_live_participants.return_value = [
            {"uuid": "uuid-a", "display_name": "Dup", "remote_alias": "ep@example.com",
             "role": "host", "remote_address": ""},
            {"uuid": "uuid-b", "display_name": "Dup", "remote_alias": "ep@example.com",
             "role": "guest", "remote_address": ""},
        ]
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_endpoint(conn, mid, endpoint_alias="ep@example.com")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/remove_endpoint",
                                  {"endpoint_alias": "ep@example.com"}, csrf)
            assert resp.status_code == 409
            data = resp.get_json()
            assert data["ok"] is False
        mock_pexip.disconnect_participant.assert_not_called()

    def test_remove_endpoint_failure_handled_safely(self, test_db):
        """Test 12: Removal failure is handled safely (DB record preserved)."""
        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "tok"
        mock_pexip.get_live_participants.return_value = [
            {"uuid": "uuid-1", "display_name": "EP", "remote_alias": "ep@example.com",
             "role": "host", "remote_address": ""},
        ]
        mock_pexip.disconnect_participant.side_effect = RuntimeError("pexip error")
        mock_pexip.get_live_participants_via_edges.return_value = []
        app = make_app(test_db, mock_pexip)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_endpoint(conn, mid, endpoint_alias="ep@example.com")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = _post_json(client, f"/api/meetings/{mid}/remove_endpoint",
                                  {"endpoint_alias": "ep@example.com"}, csrf)
            assert resp.status_code == 500
            data = resp.get_json()
            assert data["ok"] is False
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                ep = conn.execute(
                    "SELECT id FROM meeting_endpoints WHERE meeting_id = ? AND endpoint_alias = ?",
                    (mid, "ep@example.com"),
                ).fetchone()
                assert ep is not None, "DB record must be preserved when disconnect fails"

    def test_active_end_time_edit_still_works(self, test_db):
        """Test 13: Existing active end-time edit still works."""
        app = make_app(test_db)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                new_end = iso(now_utc() + timedelta(hours=3))
                resp = _post_json(client, f"/api/meetings/{mid}/edit",
                                  {"end_time": new_end, "notes": "test"}, csrf)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["ok"] is True

    def test_active_notes_edit_still_works(self, test_db):
        """Test 14: Existing active notes edit still works."""
        app = make_app(test_db)
        _create_user(test_db)
        mid = _active_meeting(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                new_end = iso(now_utc() + timedelta(hours=2))
                resp = _post_json(client, f"/api/meetings/{mid}/edit",
                                  {"end_time": new_end, "notes": "updated notes"}, csrf)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["item"]["notes"] == "updated notes"
