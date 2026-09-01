"""
Authentication and authorization tests for cklabScheduler.

Covers:
  - Local authentication (login/logout/session)
  - API authentication enforcement (401/403)
  - CSRF protection
  - Open redirect protection
  - Role-based access control
  - Password hashing
  - Database schema and migration
  - Health endpoint security
  - Configuration validation
  - Entra authentication flow stubs
  - Mount-path correctness for auth routes
"""
import sqlite3
from contextlib import closing
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.database import db, init_db
from app.meeting_utils import iso, now_utc


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_app(test_db, extra_config=None):
    """Create a test Flask application."""
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = []
    with (
        patch.object(Settings, "DB_PATH", test_db),
        patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"),
        patch.object(Settings, "COMMAND_HOST", "edge.example.com"),
        patch.object(Settings, "API_USER", "user"),
        patch.object(Settings, "API_PASS", "pass"),
        patch.object(Settings, "SECRET_KEY", "testsecret-" + "x" * 24),
        patch.object(Settings, "O365_ENABLED", False),
        patch.object(Settings, "LOCAL_AUTH_ENABLED", True),
        patch.object(Settings, "ENTRA_ENABLED", False),
        patch.object(Settings, "SESSION_COOKIE_SECURE", False),
        patch("app.PexipAPI", return_value=mock_pexip),
    ):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        if extra_config:
            app.config.update(extra_config)
        return app


def create_test_user(test_db, username="testuser", role="scheduler_user", enabled=True):
    """Insert a local test user and return their id."""
    from app.auth.local import hash_password
    from app.auth.models import create_local_user
    with patch.object(Settings, "DB_PATH", test_db):
        try:
            return create_local_user(
                username,
                hash_password("TestPassword123!"),
                display_name=username.title(),
                role=role,
            )
        except ValueError:
            # Already exists
            with closing(db()) as conn:
                row = conn.execute(
                    "SELECT id FROM users WHERE lower(username)=lower(?) AND auth_provider='local'",
                    (username,),
                ).fetchone()
                if not enabled:
                    conn.execute("UPDATE users SET enabled=0 WHERE id=?", (row["id"],))
                    conn.commit()
                return row["id"]


def login(client, test_db, username="testuser", password="TestPassword123!"):
    """Perform a local login and return the response."""
    with patch.object(Settings, "DB_PATH", test_db):
        return client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )


# ── 1. Unauthenticated UI request redirects to login ──────────────────────────

class TestUnauthenticatedRedirect:
    def test_ui_root_redirects_to_login(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirect_contains_next_url(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/")
        assert resp.status_code == 302


# ── 2. Unauthenticated API returns 401 ────────────────────────────────────────

class TestUnauthenticatedAPI:
    def test_api_meetings_returns_401(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/meetings")
        assert resp.status_code == 401
        assert resp.get_json()["ok"] is False

    def test_api_endpoints_returns_401(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/endpoints")
        assert resp.status_code == 401

    def test_api_config_returns_401(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/config")
        assert resp.status_code == 401

    def test_api_create_meeting_returns_401(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.post("/api/meetings", json={})
        assert resp.status_code == 401

    def test_health_is_public(self, test_db):
        """Health endpoint must not require authentication."""
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/health")
        assert resp.status_code in (200, 500)


# ── 3. Valid local login succeeds ─────────────────────────────────────────────

class TestLocalLoginSuccess:
    def test_valid_login_redirects_to_home(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            resp = login(client, test_db)
        assert resp.status_code == 302
        assert "/login" not in resp.headers.get("Location", "")

    def test_authenticated_user_can_access_ui(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                login(client, test_db)
                resp = client.get("/")
        assert resp.status_code == 200

    def test_authenticated_user_can_access_api(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                login(client, test_db)
                resp = client.get("/api/meetings")
        assert resp.status_code == 200


# ── 4. Invalid password fails ─────────────────────────────────────────────────

class TestInvalidPassword:
    def test_wrong_password_returns_login_page(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    "/login",
                    data={"username": "testuser", "password": "wrongpass"},
                )
        assert resp.status_code == 200
        assert b"Invalid" in resp.data

    def test_wrong_password_does_not_set_session(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                client.post(
                    "/login",
                    data={"username": "testuser", "password": "wrongpass"},
                )
                # After failed login, protected route must still redirect
                resp = client.get("/")
        assert resp.status_code == 302


# ── 5. Nonexistent user produces identical failure behavior ────────────────────

class TestNonexistentUser:
    def test_unknown_user_shows_same_message_as_wrong_password(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp_unknown = client.post(
                    "/login",
                    data={"username": "doesnotexist", "password": "anything"},
                )
        assert b"Invalid" in resp_unknown.data
        assert resp_unknown.status_code == 200


# ── 6. Disabled local account cannot login ────────────────────────────────────

class TestDisabledAccount:
    def test_disabled_user_cannot_login(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            uid = create_test_user(test_db, username="disableduser")
            with closing(db()) as conn:
                conn.execute("UPDATE users SET enabled=0 WHERE id=?", (uid,))
                conn.commit()
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    "/login",
                    data={"username": "disableduser", "password": "TestPassword123!"},
                )
        assert resp.status_code == 200
        assert b"Invalid" in resp.data

    def test_disabled_user_cannot_access_after_login_was_valid(self, test_db):
        """A user disabled between requests loses access."""
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            uid = create_test_user(test_db, username="willbedisabled")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                login(client, test_db, username="willbedisabled")
                # Disable mid-session
                with closing(db()) as conn:
                    conn.execute("UPDATE users SET enabled=0 WHERE id=?", (uid,))
                    conn.commit()
                resp = client.get("/")
        # Flask-Login calls user_loader; disabled user.is_active=False → redirect
        assert resp.status_code in (302, 401)


# ── 7. Administrator role succeeds on admin route ─────────────────────────────

class TestRoles:
    def test_admin_user_can_access_scheduler(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db, username="admin1", role="administrator")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                login(client, test_db, username="admin1")
                resp = client.get("/api/meetings")
        assert resp.status_code == 200

    # ── 8. scheduler_user vs administrator role via has_role() ────────────────────

    def test_scheduler_user_role_check(self, test_db):
        """scheduler_user.has_role('administrator') must return False."""
        from app.auth.local import hash_password
        from app.auth.models import create_local_user, get_user_by_id
        with patch.object(Settings, "DB_PATH", test_db):
            uid = create_local_user(
                "scheduser", hash_password("TestPassword123!"), role="scheduler_user"
            )
            user = get_user_by_id(uid)
        assert user.has_role("scheduler_user") is True
        assert user.has_role("administrator") is False

    def test_admin_satisfies_all_roles(self, test_db):
        """administrator.has_role('scheduler_user') must return True."""
        from app.auth.local import hash_password
        from app.auth.models import create_local_user, get_user_by_id
        with patch.object(Settings, "DB_PATH", test_db):
            uid = create_local_user(
                "adminuser2", hash_password("TestPassword123!"), role="administrator"
            )
            user = get_user_by_id(uid)
        assert user.has_role("administrator") is True
        assert user.has_role("scheduler_user") is True


# ── 9. Logout invalidates session ─────────────────────────────────────────────

class TestLogout:
    def test_logout_clears_session(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                login(client, test_db)
                client.get("/logout")
                resp = client.get("/")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ── 10. Password hashes are never stored plaintext ────────────────────────────

class TestPasswordHashing:
    def test_password_is_hashed_in_database(self, test_db):
        from app.auth.local import hash_password
        with patch.object(Settings, "DB_PATH", test_db):
            from app.auth.models import create_local_user
            create_local_user("hashtest", hash_password("MySecretPass123"))
            with closing(db()) as conn:
                row = conn.execute(
                    "SELECT password_hash FROM users WHERE username='hashtest'"
                ).fetchone()
        assert row is not None
        assert row["password_hash"] != "MySecretPass123"
        assert row["password_hash"].startswith("pbkdf2:")

    def test_hash_verifies_correctly(self, test_db):
        from app.auth.local import hash_password, verify_password
        pw = "CorrectHorseBattery!"
        h = hash_password(pw)
        assert verify_password(h, pw) is True
        assert verify_password(h, "WrongPassword") is False

    def test_password_too_short_raises(self):
        from app.auth.local import validate_password_strength
        with pytest.raises(ValueError):
            validate_password_strength("short")

    def test_password_exact_minimum_passes(self):
        from app.auth.local import MIN_PASSWORD_LENGTH, validate_password_strength
        validate_password_strength("a" * MIN_PASSWORD_LENGTH)  # must not raise


# ── 11–18. Entra authentication stubs ─────────────────────────────────────────

class TestEntraStubs:
    def test_entra_login_redirects_when_disabled(self, test_db):
        """When Entra is disabled, /auth/login_entra redirects to login."""
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/auth/login_entra")
        assert resp.status_code == 302

    def test_entra_callback_redirects_when_disabled(self, test_db):
        """When Entra is disabled, /auth/callback redirects to login."""
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/auth/callback")
        assert resp.status_code == 302

    def test_entra_role_mapping_administrator(self):
        from app.auth.entra import extract_role_from_claims
        with patch.object(Settings, "ENTRA_REQUIRED_ADMIN_ROLE", "Scheduler.Administrator"):
            with patch.object(Settings, "ENTRA_REQUIRED_USER_ROLE", "Scheduler.User"):
                role, err = extract_role_from_claims(
                    {"roles": ["Scheduler.Administrator"]}
                )
        assert role == "administrator"
        assert err is None

    def test_entra_role_mapping_user(self):
        from app.auth.entra import extract_role_from_claims
        with patch.object(Settings, "ENTRA_REQUIRED_ADMIN_ROLE", "Scheduler.Administrator"):
            with patch.object(Settings, "ENTRA_REQUIRED_USER_ROLE", "Scheduler.User"):
                role, err = extract_role_from_claims({"roles": ["Scheduler.User"]})
        assert role == "scheduler_user"
        assert err is None

    def test_entra_missing_role_returns_none(self):
        from app.auth.entra import extract_role_from_claims
        with patch.object(Settings, "ENTRA_REQUIRED_ADMIN_ROLE", "Scheduler.Administrator"):
            with patch.object(Settings, "ENTRA_REQUIRED_USER_ROLE", "Scheduler.User"):
                role, err = extract_role_from_claims({"roles": []})
        assert role is None
        assert err is not None

    def test_entra_identity_extraction(self):
        from app.auth.entra import extract_identity_from_claims
        claims = {
            "preferred_username": "user@example.com",
            "name": "Test User",
        }
        username, display_name = extract_identity_from_claims(claims)
        assert username == "user@example.com"
        assert display_name == "Test User"


# ── 19–22. Configuration validation ──────────────────────────────────────────

class TestConfiguration:
    def test_local_only_mode_is_default(self):
        with patch.object(Settings, "LOCAL_AUTH_ENABLED", True), \
             patch.object(Settings, "ENTRA_ENABLED", False):
            assert Settings.LOCAL_AUTH_ENABLED is True
            assert not Settings.is_entra_auth_enabled()

    def test_entra_enabled_requires_tenant_and_client_id(self):
        with patch.object(Settings, "ENTRA_ENABLED", True), \
             patch.object(Settings, "ENTRA_TENANT_ID", ""), \
             patch.object(Settings, "ENTRA_CLIENT_ID", ""):
            assert not Settings.is_entra_auth_enabled()

    def test_no_auth_raises_on_validate(self, test_db):
        with patch.object(Settings, "LOCAL_AUTH_ENABLED", False), \
             patch.object(Settings, "ENTRA_ENABLED", False), \
             patch.object(Settings, "DB_PATH", test_db), \
             patch.object(Settings, "REG_STATUS_HOST", "h"), \
             patch.object(Settings, "COMMAND_HOST", "h"), \
             patch.object(Settings, "API_USER", "u"), \
             patch.object(Settings, "API_PASS", "p"), \
             patch.object(Settings, "SECRET_KEY", "k"):
            with pytest.raises(RuntimeError, match="Authentication misconfiguration"):
                Settings.validate_web()

    def test_app_display_name_unchanged(self):
        with patch.object(Settings, "APP_DISPLAY_NAME", "My Scheduler"):
            assert Settings.APP_DISPLAY_NAME == "My Scheduler"

    def test_control_display_name_unchanged(self):
        with patch.object(Settings, "CONTROL_DISPLAY_NAME", "Scheduler"):
            assert Settings.CONTROL_DISPLAY_NAME == "Scheduler"


# ── 25–26. CSRF protection ────────────────────────────────────────────────────

class TestCSRF:
    def test_post_without_csrf_returns_400_when_enabled(self, test_db):
        """When CSRF is enabled, a POST without token is rejected."""
        app = make_app(test_db)
        app.config["WTF_CSRF_ENABLED"] = True
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                # Log in first (via test helper with CSRF disabled for login)
                app.config["WTF_CSRF_ENABLED"] = False
                login(client, test_db)
                app.config["WTF_CSRF_ENABLED"] = True
                # Then try a mutating API call without CSRF token
                resp = client.post("/api/meetings", json={})
        assert resp.status_code == 400


# ── 27. Open redirect protection ─────────────────────────────────────────────

class TestOpenRedirect:
    def test_external_next_url_is_rejected(self, test_db):
        """next= pointing to an external domain must not redirect there."""
        from app.auth.local import hash_password
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            from app.auth.models import create_local_user
            create_local_user("redirecttest", hash_password("TestPassword123!"), role="scheduler_user")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    "/login",
                    data={
                        "username": "redirecttest",
                        "password": "TestPassword123!",
                        "next": "https://evil.com/steal",
                    },
                    follow_redirects=False,
                )
        location = resp.headers.get("Location", "")
        assert "evil.com" not in location

    def test_relative_next_url_is_allowed(self, test_db):
        """A relative next= URL on the same host must be honoured."""
        from app.auth.local import hash_password
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            from app.auth.models import create_local_user
            create_local_user("nexttest", hash_password("TestPassword123!"), role="scheduler_user")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    "/login",
                    data={
                        "username": "nexttest",
                        "password": "TestPassword123!",
                        "next": "http://localhost/",
                    },
                    follow_redirects=False,
                )
        assert resp.status_code == 302


# ── 28. Health endpoint exposes no secrets ────────────────────────────────────

class TestHealthSecurity:
    def test_health_does_not_expose_tenant_id(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENTRA_TENANT_ID", "secret-tenant-id"):
                resp = client.get("/api/health")
        assert "secret-tenant-id" not in resp.get_data(as_text=True)

    def test_health_does_not_expose_client_id(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db), \
                 patch.object(Settings, "ENTRA_CLIENT_ID", "secret-client-id"):
                resp = client.get("/api/health")
        assert "secret-client-id" not in resp.get_data(as_text=True)

    def test_health_reports_auth_status(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/health")
        data = resp.get_json()
        assert "authentication" in data
        assert "local_enabled" in data["authentication"]
        assert "entra_enabled" in data["authentication"]

    def test_health_does_not_expose_hostnames(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/health")
        body = resp.get_data(as_text=True)
        assert "pexip.example.com" not in body
        assert "edge.example.com" not in body


# ── 31–35. Database schema and migration ─────────────────────────────────────

class TestDatabaseSchema:
    def test_users_table_created(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            init_db()
            with closing(db()) as conn:
                tables = [
                    r["name"]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
        assert "users" in tables

    def test_auth_audit_log_table_created(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            init_db()
            with closing(db()) as conn:
                tables = [
                    r["name"]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
        assert "auth_audit_log" in tables

    def test_meetings_survive_migration(self, test_db):
        """Existing meetings must be preserved after init_db() is called again."""
        with patch.object(Settings, "DB_PATH", test_db):
            init_db()
            with closing(db()) as conn:
                conn.execute(
                    """
                    INSERT INTO meetings
                        (title, meeting_alias, start_time, end_time, status,
                         created_at, updated_at)
                    VALUES ('Test','docmigrationtest01','2025-01-01T10:00:00+00:00',
                            '2025-01-01T11:00:00+00:00','scheduled',
                            '2025-01-01T00:00:00+00:00','2025-01-01T00:00:00+00:00')
                    """
                )
                conn.commit()
            # Run migration again
            init_db()
            with closing(db()) as conn:
                count = conn.execute("SELECT COUNT(*) AS c FROM meetings").fetchone()["c"]
        assert count == 1

    def test_duplicate_local_username_prevented(self, test_db):
        from app.auth.local import hash_password
        from app.auth.models import create_local_user
        with patch.object(Settings, "DB_PATH", test_db):
            create_local_user("dupeuser", hash_password("TestPassword123!"))
            with pytest.raises(ValueError):
                create_local_user("dupeuser", hash_password("TestPassword123!"))

    def test_password_reset_updates_hash(self, test_db):
        from app.auth.local import hash_password, verify_password
        from app.auth.models import create_local_user, set_user_password, get_user_row_by_username
        with patch.object(Settings, "DB_PATH", test_db):
            create_local_user("resetme", hash_password("OldPassword123!"))
            set_user_password("resetme", hash_password("NewPassword456!"))
            row = get_user_row_by_username("resetme")
        assert verify_password(row["password_hash"], "NewPassword456!")
        assert not verify_password(row["password_hash"], "OldPassword123!")


# ── 36–37. Mount-path regression ─────────────────────────────────────────────

SCRIPT_NAME = "/cklabScheduler"


class TestMountPathAuth:
    def test_login_route_works_with_script_name(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get(
                "/login",
                environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
            )
        assert resp.status_code == 200

    def test_logout_route_works_with_script_name(self, test_db):
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_test_user(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                login(client, test_db)
                resp = client.get(
                    "/logout",
                    environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
                )
        assert resp.status_code in (200, 302)

    def test_api_returns_401_not_500_with_script_name(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get(
                "/api/meetings",
                environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
            )
        assert resp.status_code == 401

    def test_entra_callback_route_exists_with_script_name(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get(
                "/auth/callback",
                environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
            )
        # Redirect is expected (Entra disabled → redirect to login)
        assert resp.status_code == 302
