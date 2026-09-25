"""
Behavioral tests for endpoint display-name overrides.

Covers:
  - Persistence: admin sets override, GET reflects it, DELETE removes it
  - Authorization: administrator can modify, scheduler_user and unauthenticated cannot
  - Validation: empty, oversized, control characters, Unicode, HTML-like names
  - Identity safety: alias, dial target, live matching unaffected by override
  - Active meeting safety: add/remove/redial still use original alias
  - Idempotency: DELETE on non-existent override returns 200
"""
import json
import os
from contextlib import closing
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.database import db, init_db
from app.meeting_utils import iso, now_utc, normalize_alias, load_display_overrides
from tests.conftest import get_csrf_token, insert_meeting, insert_endpoint


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_app(test_db):
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = [
        {"alias": "wspex1", "display_name": "Workstation 1", "protocol": "sip", "is_registered": True, "node": ""},
        {"alias": "wspex2", "display_name": "Workstation 2", "protocol": "sip", "is_registered": True, "node": ""},
    ]
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


def _create_user(test_db, username="testadmin", role="administrator"):
    from app.auth.local import hash_password
    from app.auth.models import create_local_user
    with patch.object(Settings, "DB_PATH", test_db):
        try:
            create_local_user(username, hash_password(os.environ["TEST_USER_PASSWORD"]), role=role)
        except ValueError:
            pass


def _login(client, test_db, username="testadmin"):
    with patch.object(Settings, "DB_PATH", test_db):
        csrf = get_csrf_token(client, "/login")
        client.post(
            "/login",
            data={
                "username": username,
                "password": os.environ["TEST_USER_PASSWORD"],
                "csrf_token": csrf,
            },
            follow_redirects=True,
        )


def _csrf(client, test_db):
    with patch.object(Settings, "DB_PATH", test_db):
        return get_csrf_token(client, "/")


def _put(client, test_db, endpoint_alias, display_name, csrf=None):
    if csrf is None:
        csrf = _csrf(client, test_db)
    with patch.object(Settings, "DB_PATH", test_db):
        return client.put(
            "/api/endpoints/display-name",
            data=json.dumps({"endpoint_alias": endpoint_alias, "display_name": display_name}),
            headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
        )


def _delete(client, test_db, endpoint_alias, csrf=None):
    if csrf is None:
        csrf = _csrf(client, test_db)
    with patch.object(Settings, "DB_PATH", test_db):
        return client.delete(
            "/api/endpoints/display-name",
            data=json.dumps({"endpoint_alias": endpoint_alias}),
            headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
        )


def _get_endpoints(client, test_db):
    with patch.object(Settings, "DB_PATH", test_db):
        return client.get("/api/endpoints")


# ── Persistence ───────────────────────────────────────────────────────────────

class TestPersistence:
    def test_admin_sets_override_and_get_reflects_custom_name(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "Cardiology Workstation")
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

            resp2 = _get_endpoints(client, test_db)
            items = resp2.get_json()["items"]
            ep = next(i for i in items if i["alias"] == "wspex1")
            assert ep["display_name"] == "Cardiology Workstation"

    def test_get_exposes_pexip_display_name_separately(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")

            resp = _get_endpoints(client, test_db)
            items = resp.get_json()["items"]
            ep = next(i for i in items if i["alias"] == "wspex1")
            assert ep["pexip_display_name"] == "Workstation 1"
            assert ep["custom_display_name"] == "Cardiology Workstation"
            assert ep["display_name"] == "Cardiology Workstation"

    def test_no_override_returns_pexip_name(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _get_endpoints(client, test_db)
            items = resp.get_json()["items"]
            ep = next(i for i in items if i["alias"] == "wspex1")
            assert ep["display_name"] == "Workstation 1"
            assert ep["pexip_display_name"] == "Workstation 1"
            assert ep["custom_display_name"] is None

    def test_override_survives_repeated_endpoint_refresh(self, test_db):
        """Calling GET /api/endpoints multiple times does not erase the override."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")
            for _ in range(3):
                resp = _get_endpoints(client, test_db)
                ep = next(i for i in resp.get_json()["items"] if i["alias"] == "wspex1")
                assert ep["display_name"] == "Cardiology Workstation"

    def test_override_survives_init_db_reinvocation(self, test_db):
        """init_db() called again (simulating upgrade) does not drop override data."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")

        with patch.object(Settings, "DB_PATH", test_db):
            init_db()
            with closing(db()) as conn:
                overrides = load_display_overrides(conn)
        assert overrides.get(normalize_alias("wspex1")) == "Cardiology Workstation"

    def test_restore_default_removes_override(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")
            resp = _delete(client, test_db, "wspex1")
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

            resp2 = _get_endpoints(client, test_db)
            ep = next(i for i in resp2.get_json()["items"] if i["alias"] == "wspex1")
            assert ep["display_name"] == "Workstation 1"
            assert ep["custom_display_name"] is None

    def test_delete_nonexistent_override_is_idempotent(self, test_db):
        """DELETE with no override set returns 200 (idempotent)."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _delete(client, test_db, "wspex1")
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_update_existing_override(self, test_db):
        """Setting override twice updates the custom name."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "First Name")
            _put(client, test_db, "wspex1", "Second Name")

            resp = _get_endpoints(client, test_db)
            ep = next(i for i in resp.get_json()["items"] if i["alias"] == "wspex1")
            assert ep["display_name"] == "Second Name"

    def test_duplicate_display_names_allowed(self, test_db):
        """Two endpoints may share the same custom display name."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Shared Room")
            _put(client, test_db, "wspex2", "Shared Room")

            resp = _get_endpoints(client, test_db)
            items = resp.get_json()["items"]
            names = [i["display_name"] for i in items if i["alias"] in ("wspex1", "wspex2")]
            assert names.count("Shared Room") == 2


# ── Authorization ─────────────────────────────────────────────────────────────

class TestAuthorization:
    def test_administrator_can_put(self, test_db):
        app = make_app(test_db)
        _create_user(test_db, role="administrator")
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "Admin Name")
            assert resp.status_code == 200

    def test_administrator_can_delete(self, test_db):
        app = make_app(test_db)
        _create_user(test_db, role="administrator")
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Admin Name")
            resp = _delete(client, test_db, "wspex1")
            assert resp.status_code == 200

    def test_scheduler_user_cannot_put(self, test_db):
        app = make_app(test_db)
        _create_user(test_db, username="normaluser", role="scheduler_user")
        with app.test_client() as client:
            _login(client, test_db, username="normaluser")
            csrf = _csrf(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.put(
                    "/api/endpoints/display-name",
                    data=json.dumps({"endpoint_alias": "wspex1", "display_name": "Evil Name"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 403
            assert resp.get_json()["ok"] is False

    def test_scheduler_user_cannot_delete(self, test_db):
        app = make_app(test_db)
        _create_user(test_db, role="administrator")
        _create_user(test_db, username="normaluser", role="scheduler_user")
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Admin Name")

        with app.test_client() as client:
            _login(client, test_db, username="normaluser")
            resp = _delete(client, test_db, "wspex1")
            assert resp.status_code == 403

    def test_unauthenticated_cannot_put(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.put(
                    "/api/endpoints/display-name",
                    data=json.dumps({"endpoint_alias": "wspex1", "display_name": "X"}),
                    headers={"Content-Type": "application/json"},
                )
            assert resp.status_code in (400, 401, 403)

    def test_unauthenticated_cannot_delete(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.delete(
                    "/api/endpoints/display-name",
                    data=json.dumps({"endpoint_alias": "wspex1"}),
                    headers={"Content-Type": "application/json"},
                )
            assert resp.status_code in (400, 401, 403)

    def test_scheduler_user_can_read_endpoints(self, test_db):
        """GET /api/endpoints (read-only) is accessible to any authenticated user."""
        app = make_app(test_db)
        _create_user(test_db, username="normaluser", role="scheduler_user")
        with app.test_client() as client:
            _login(client, test_db, username="normaluser")
            resp = _get_endpoints(client, test_db)
            assert resp.status_code == 200
            assert resp.get_json()["ok"] is True

    def test_local_breakglass_admin_can_put(self, test_db):
        """Local user with role='administrator' (break-glass) can set overrides."""
        app = make_app(test_db)
        _create_user(test_db, username="breakglass", role="administrator")
        with app.test_client() as client:
            _login(client, test_db, username="breakglass")
            resp = _put(client, test_db, "wspex1", "Break Glass Name")
            assert resp.status_code == 200


# ── Input Validation ──────────────────────────────────────────────────────────

class TestValidation:
    def _setup(self, test_db):
        app = make_app(test_db)
        _create_user(test_db)
        return app

    def test_whitespace_trimmed(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "  Trimmed Name  ")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["custom_display_name"] == "Trimmed Name"

    def test_empty_name_rejected(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "")
            assert resp.status_code == 400
            assert resp.get_json()["ok"] is False

    def test_whitespace_only_rejected(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "   ")
            assert resp.status_code == 400

    def test_oversized_name_rejected(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "A" * 201)
            assert resp.status_code == 400

    def test_max_length_accepted(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "A" * 200)
            assert resp.status_code == 200

    def test_control_character_rejected(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "Bad\x00Name")
            assert resp.status_code == 400

    def test_escape_sequence_rejected(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "Bad\x1bName")
            assert resp.status_code == 400

    def test_unicode_accepted(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _put(client, test_db, "wspex1", "Cardiology — Suite 3 (东)")
            assert resp.status_code == 200

    def test_html_like_name_stored_as_literal_text(self, test_db):
        """HTML/script-like names are stored without modification (not executed)."""
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            name = "<script>alert(1)</script>"
            resp = _put(client, test_db, "wspex1", name)
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["custom_display_name"] == name

    def test_missing_endpoint_alias_rejected(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            csrf = _csrf(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.put(
                    "/api/endpoints/display-name",
                    data=json.dumps({"display_name": "No Alias"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400

    def test_missing_display_name_rejected(self, test_db):
        app = self._setup(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            csrf = _csrf(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.put(
                    "/api/endpoints/display-name",
                    data=json.dumps({"endpoint_alias": "wspex1"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 400


# ── Identity Safety ───────────────────────────────────────────────────────────

class TestIdentitySafety:
    def test_alias_field_unchanged_after_override(self, test_db):
        """The alias field in GET /api/endpoints is never the custom name."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")
            resp = _get_endpoints(client, test_db)
            ep = next(i for i in resp.get_json()["items"] if i["display_name"] == "Cardiology Workstation")
            assert ep["alias"] == "wspex1"

    def test_normalization_case_insensitive_lookup(self, test_db):
        """Override stored for 'WSPEX1' is found when queried via lowercase alias."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "WSPEX1", "Upper Alias Name")
            resp = _get_endpoints(client, test_db)
            ep = next(i for i in resp.get_json()["items"] if i["alias"] == "wspex1")
            assert ep["display_name"] == "Upper Alias Name"

    def test_sip_prefix_normalized_for_lookup(self, test_db):
        """Override stored with 'sip:wspex1' resolves to the same alias key."""
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "sip:wspex1", "SIP Override")
            resp = _get_endpoints(client, test_db)
            ep = next(i for i in resp.get_json()["items"] if i["alias"] == "wspex1")
            assert ep["display_name"] == "SIP Override"

    def test_dial_still_uses_original_alias(self, test_db):
        """Meeting creation with an overridden endpoint stores the original alias in DB."""
        now = now_utc()
        app = make_app(test_db)
        _create_user(test_db)
        with app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")
            csrf = _csrf(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    "/api/meetings",
                    data=json.dumps({
                        "title": "Test",
                        "start_time": iso(now + timedelta(hours=1)),
                        "end_time": iso(now + timedelta(hours=2)),
                        "endpoints": [
                            {"endpoint_alias": "wspex1", "display_name": "Cardiology Workstation"}
                        ],
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            data = resp.get_json()
            assert data["ok"] is True
            ep = data["item"]["endpoints"][0]
            assert ep["endpoint_alias"] == "wspex1"

    def test_live_matching_uses_pexip_display_name_not_custom(self, test_db):
        """After setting an override, meeting endpoint live=True is still determined by
        the original Pexip-reported participant name/alias, not the custom display name."""
        now = now_utc()
        app = make_app(test_db)
        _create_user(test_db)

        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = [
            {"alias": "wspex1", "display_name": "Workstation 1", "protocol": "sip",
             "is_registered": True, "node": ""},
        ]
        # Pexip live participant uses the original Pexip name
        mock_pexip.get_live_participants_via_edges.return_value = [
            {"display_name": "Workstation 1", "remote_alias": "wspex1",
             "uuid": "uuid-1234", "role": "host"},
        ]

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
            live_app = create_app()
            live_app.config["TESTING"] = True

        with live_app.test_client() as client:
            _login(client, test_db)
            # Set the display name override
            _put(client, test_db, "wspex1", "Cardiology Workstation")

            # Create a meeting with a started_at so live participants are fetched
            with patch.object(Settings, "DB_PATH", test_db):
                with closing(db()) as conn:
                    mid = insert_meeting(
                        conn,
                        status="started",
                        started_at=iso(now - timedelta(minutes=5)),
                        start_time=iso(now - timedelta(minutes=5)),
                        end_time=iso(now + timedelta(hours=1)),
                    )
                    conn.execute(
                        "INSERT INTO meeting_endpoints (meeting_id, endpoint_alias, display_name, role, status) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (mid, "wspex1", "Workstation 1", "host", "dialed"),
                    )
                    conn.commit()

            csrf = _csrf(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(f"/api/meetings?date={now.date().isoformat()}")
            meetings = resp.get_json()["items"]
            m = next(m for m in meetings if m["id"] == mid)
            ep = m["endpoints"][0]
            # Display name is the custom override
            assert ep["display_name"] == "Cardiology Workstation"
            # Pexip display name is the original
            assert ep["pexip_display_name"] == "Workstation 1"
            # Live state is correct despite the name difference
            assert ep["live"] is True
            # Dial identity is unchanged
            assert ep["endpoint_alias"] == "wspex1"

    def test_active_remove_disconnects_by_uuid_not_custom_name(self, test_db):
        """Active endpoint removal still identifies participant by UUID/alias, not custom name."""
        now = now_utc()
        app = make_app(test_db)
        _create_user(test_db)

        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "test-token"
        mock_pexip.get_live_participants.return_value = [
            {"display_name": "Workstation 1", "remote_alias": "wspex1",
             "uuid": "part-uuid-999", "role": "host"},
        ]
        mock_pexip.disconnect_participant.return_value = {}
        mock_pexip.release_control_token.return_value = {}

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
            remove_app = create_app()
            remove_app.config["TESTING"] = True

        with remove_app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")

            with patch.object(Settings, "DB_PATH", test_db):
                with closing(db()) as conn:
                    mid = insert_meeting(
                        conn,
                        status="started",
                        started_at=iso(now - timedelta(minutes=5)),
                        start_time=iso(now - timedelta(minutes=5)),
                        end_time=iso(now + timedelta(hours=1)),
                    )
                    conn.execute(
                        "INSERT INTO meeting_endpoints (meeting_id, endpoint_alias, display_name, role, status) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (mid, "wspex1", "Workstation 1", "host", "dialed"),
                    )
                    conn.commit()

            csrf = _csrf(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    f"/api/meetings/{mid}/remove_endpoint",
                    data=json.dumps({"endpoint_alias": "wspex1"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 200
            # Disconnect was called with the UUID, not the display name
            mock_pexip.disconnect_participant.assert_called_once()
            call_args = mock_pexip.disconnect_participant.call_args
            assert call_args[0][1] == "part-uuid-999"

    def test_dial_again_uses_original_alias(self, test_db):
        """Dial Again (redial) targets the original endpoint alias, not the custom name."""
        now = now_utc()
        app = make_app(test_db)
        _create_user(test_db)

        mock_pexip = MagicMock()
        mock_pexip.list_registered_endpoints.return_value = []
        mock_pexip.request_control_token.return_value = "token"
        mock_pexip.dial_endpoint_to_meeting.return_value = {"result": {}}
        mock_pexip.release_control_token.return_value = {}

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
            redial_app = create_app()
            redial_app.config["TESTING"] = True

        with redial_app.test_client() as client:
            _login(client, test_db)
            _put(client, test_db, "wspex1", "Cardiology Workstation")

            with patch.object(Settings, "DB_PATH", test_db):
                with closing(db()) as conn:
                    mid = insert_meeting(
                        conn,
                        status="started",
                        started_at=iso(now - timedelta(minutes=5)),
                        start_time=iso(now - timedelta(minutes=5)),
                        end_time=iso(now + timedelta(hours=1)),
                    )
                    conn.execute(
                        "INSERT INTO meeting_endpoints (meeting_id, endpoint_alias, display_name, role, status) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (mid, "wspex1", "Workstation 1", "host", "error"),
                    )
                    conn.commit()

            csrf = _csrf(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    f"/api/meetings/{mid}/redial_endpoint",
                    data=json.dumps({"endpoint_alias": "wspex1"}),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
            assert resp.status_code == 200
            mock_pexip.dial_endpoint_to_meeting.assert_called_once()
            call_args = mock_pexip.dial_endpoint_to_meeting.call_args
            # Second arg is endpoint_alias — must be original, not custom name
            assert call_args[0][1] == "wspex1"
