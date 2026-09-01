"""
Regression tests for the /cklabScheduler subpath deployment.

Background
----------
Phase 3 (r3) uncovered that Apache ProxyPass was stripping the /cklabScheduler
prefix before forwarding to Gunicorn:

    OLD (broken):
      ProxyPass /cklabScheduler/ http://127.0.0.1:5080/
      → Apache forwards /api/health
      → Gunicorn SCRIPT_NAME=/cklabScheduler tries path.split('/cklabScheduler',1)[1]
      → '/api/health'.split('/cklabScheduler',1) = ['/api/health']  (one element)
      → ['/api/health'][1]  → IndexError — Flask never reached

    NEW (fixed):
      ProxyPass /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/
      → Apache forwards /cklabScheduler/api/health
      → Gunicorn splits correctly → PATH_INFO=/api/health, SCRIPT_NAME=/cklabScheduler
      → Flask receives PATH_INFO=/api/health, routes correctly → 200

The tests below verify:
  1. The Gunicorn SCRIPT_NAME split math (pure unit tests, no Flask)
  2. Flask routes work when Gunicorn-processed WSGI environ is presented
  3. request.script_root is populated for the template's window.APP_ROOT
  4. url_for() generates paths that include /cklabScheduler when SCRIPT_NAME is set
"""
import contextlib
import re
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings

SCRIPT_NAME = "/cklabScheduler"


def make_app(test_db):
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = []
    with patch.object(Settings, "DB_PATH", test_db), \
         patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "API_USER", "user"), \
         patch.object(Settings, "API_PASS", "pass"), \
         patch.object(Settings, "SECRET_KEY", "testsecret"), \
         patch.object(Settings, "O365_ENABLED", False), \
         patch.object(Settings, "LOCAL_AUTH_ENABLED", True), \
         patch.object(Settings, "ENTRA_ENABLED", False), \
         patch.object(Settings, "SESSION_COOKIE_SECURE", False), \
         patch("app.PexipAPI", return_value=mock_pexip):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        return app


# ── Pure math: Gunicorn SCRIPT_NAME splitting ─────────────────────────────────

class TestGunicornScriptNameSplitting:
    """
    Verify the string arithmetic that Gunicorn performs in http/wsgi.py:
        path_info = path_info.split(script_name, 1)[1]
    """

    def test_split_succeeds_when_prefix_preserved(self):
        """Apache NEW config: full path forwarded → Gunicorn split succeeds."""
        full_path = "/cklabScheduler/api/health"
        parts = full_path.split(SCRIPT_NAME, 1)
        assert len(parts) == 2, "Split must yield two parts when prefix is present"
        assert parts[1] == "/api/health"

    def test_split_raises_when_prefix_stripped(self):
        """Apache OLD config: prefix stripped → Gunicorn split raises IndexError."""
        stripped_path = "/api/health"
        parts = stripped_path.split(SCRIPT_NAME, 1)
        assert len(parts) == 1, "Only one part when prefix is absent"
        with pytest.raises(IndexError):
            _ = parts[1]  # This is exactly what Gunicorn does → the bug

    def test_split_succeeds_for_all_known_paths(self):
        """Verify the split works for every path the app serves."""
        paths = [
            "/cklabScheduler/",
            "/cklabScheduler/api/health",
            "/cklabScheduler/api/meetings",
            "/cklabScheduler/api/endpoints",
            "/cklabScheduler/api/config",
            "/cklabScheduler/static/app.js",
            "/cklabScheduler/static/styles.css",
        ]
        for full_path in paths:
            parts = full_path.split(SCRIPT_NAME, 1)
            assert len(parts) == 2, f"Split failed for {full_path!r}"
            path_info = parts[1]
            assert path_info == "" or path_info.startswith("/"), \
                f"Unexpected PATH_INFO {path_info!r} for {full_path!r}"


# ── Flask routing under Gunicorn-processed WSGI environ ──────────────────────

class TestFlaskRoutingWithScriptName:
    """
    Simulate the WSGI environ that Gunicorn presents to Flask after it has
    stripped SCRIPT_NAME from PATH_INFO.  Flask must route correctly.

    The test client call:
        client.get('/api/health', environ_overrides={'SCRIPT_NAME': '/cklabScheduler'})
    sets:
        PATH_INFO   = /api/health          (the path we request)
        SCRIPT_NAME = /cklabScheduler      (what Gunicorn sets after stripping)
    which is exactly what Flask receives in the corrected architecture.
    """

    def test_health_routes_with_script_name(self, test_db):
        """Health endpoint returns JSON with SCRIPT_NAME set."""
        from app.database import db
        from app.meeting_utils import iso, now_utc
        with patch.object(Settings, "DB_PATH", test_db):
            with contextlib.closing(db()) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO scheduler_heartbeat "
                    "(id, last_seen, worker_pid, worker_start) VALUES (1, ?, ?, ?)",
                    (iso(now_utc() - timedelta(seconds=5)), 9999, iso(now_utc())),
                )
                conn.commit()
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(
                    "/api/health",
                    environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_meetings_routes_with_script_name(self, test_db):
        """Meetings endpoint returns 401 (auth required) with SCRIPT_NAME set — verifies routing."""
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(
                    "/api/meetings",
                    environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
                )
        # 401 confirms Flask routed to the meetings handler, which enforces auth.
        assert resp.status_code == 401

    def test_endpoints_routes_with_script_name(self, test_db):
        """Endpoints list returns 401 (auth required) with SCRIPT_NAME set — verifies routing."""
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(
                    "/api/endpoints",
                    environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
                )
        assert resp.status_code == 401

    def test_config_routes_with_script_name(self, test_db):
        """Config endpoint returns 401 (auth required) with SCRIPT_NAME set — verifies routing."""
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(
                    "/api/config",
                    environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
                )
        assert resp.status_code == 401

    def test_ui_root_routes_with_script_name(self, test_db):
        """UI index redirects to login with SCRIPT_NAME set — verifies routing."""
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(
                    "/",
                    environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
                )
        # 302 confirms Flask routed to the UI handler, which enforces auth.
        assert resp.status_code == 302


# ── SCRIPT_NAME propagation to template and url_for ──────────────────────────

class TestScriptNamePropagation:
    """
    Verify that SCRIPT_NAME flows through to the Jinja2 template as APP_ROOT
    and that url_for() generates fully-prefixed paths.
    """

    def test_app_root_in_rendered_html(self, test_db):
        """
        The index.html template renders:
            window.APP_ROOT = {{ request.script_root|tojson }};
        When SCRIPT_NAME=/cklabScheduler, this must render as:
            window.APP_ROOT = "/cklabScheduler";
        ensuring all frontend API calls use the correct prefix.
        Requires an authenticated session to render the main page.
        """
        from app.auth.local import hash_password
        from app.auth.models import create_local_user
        with patch.object(Settings, "DB_PATH", test_db):
            create_local_user("scriptuser", hash_password("TestPassword123!"), role="scheduler_user")
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                # Log in first (no SCRIPT_NAME needed for login itself)
                client.post("/login", data={"username": "scriptuser", "password": "TestPassword123!"})
                # Now request the main page with SCRIPT_NAME
                resp = client.get(
                    "/",
                    environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
                )
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # tojson renders the string with surrounding double-quotes
        assert f'"{SCRIPT_NAME}"' in body, \
            f"Expected '\"{ SCRIPT_NAME }\"' in rendered HTML for window.APP_ROOT"

    def test_static_url_for_includes_script_name(self, test_db):
        """
        url_for('static', filename='app.js') with SCRIPT_NAME set must return
        /cklabScheduler/static/app.js so static asset links in the template are correct.
        """
        from flask import url_for
        app = make_app(test_db)
        with app.test_request_context(
            "/static/app.js",
            environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
        ):
            url = url_for("static", filename="app.js")
        assert url == f"{SCRIPT_NAME}/static/app.js", \
            f"url_for('static') returned {url!r}, expected {SCRIPT_NAME}/static/app.js"

    def test_health_url_for_includes_script_name(self, test_db):
        """
        url_for for the health endpoint includes /cklabScheduler when SCRIPT_NAME is set.
        """
        from flask import url_for
        app = make_app(test_db)
        with app.test_request_context(
            "/api/health",
            environ_overrides={"SCRIPT_NAME": SCRIPT_NAME},
        ):
            url = url_for("health.api_health")
        assert url == f"{SCRIPT_NAME}/api/health", \
            f"url_for('health.api_health') returned {url!r}"


# ── Apache ProxyPass migration detection (r2 → r3) ───────────────────────────

class TestApacheProxyPassMigration:
    """
    Verify the detection and migration logic used by upgrade.sh's Apache step.

    upgrade.sh detects the r2 broken ProxyPass pattern and applies a surgical
    sed substitution to produce the r3 correct format.  These tests use Python
    regex to mirror the same grep/sed logic so correctness can be verified
    without running a shell script.

    Detection patterns (mirrors grep -qE in upgrade.sh):
      r3 correct: ProxyPass /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/
      r2 broken:  ProxyPass /cklabScheduler/ http://127.0.0.1:5080/   ← no prefix

    Sed applied when r2 detected (upgrade.sh):
      sed '/cklabScheduler/ s|http://127.0.0.1:5080/[[:space:]]*$|http://127.0.0.1:5080/cklabScheduler/|'
    """

    # Representative Apache config snippets for each scenario
    R2_CONF = (
        "    ProxyPass        /cklabScheduler/ http://127.0.0.1:5080/\n"
        "    ProxyPassReverse /cklabScheduler/ http://127.0.0.1:5080/\n"
    )
    R3_CONF = (
        "    ProxyPass        /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/\n"
        "    ProxyPassReverse /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/\n"
    )
    # Custom target (different host/port — admin-managed, must not be touched)
    CUSTOM_CONF = (
        "    ProxyPass        /cklabScheduler/ http://10.0.0.5:8080/\n"
        "    ProxyPassReverse /cklabScheduler/ http://10.0.0.5:8080/\n"
    )

    @staticmethod
    def _is_r3(text):
        """Mirror of: grep -qE 'ProxyPass.*/cklabScheduler/.*5080/cklabScheduler/'"""
        return bool(re.search(
            r"ProxyPass\s+/cklabScheduler/\s+http://127\.0\.0\.1:5080/cklabScheduler/",
            text,
        ))

    @staticmethod
    def _is_r2(text):
        """Mirror of: grep -qE 'ProxyPass.*/cklabScheduler/.*5080/[[:space:]]*$'"""
        return bool(re.search(
            r"ProxyPass\s+/cklabScheduler/\s+http://127\.0\.0\.1:5080/\s*$",
            text,
            re.MULTILINE,
        ))

    @staticmethod
    def _apply_migration(text):
        """
        Mirror of the sed in upgrade.sh:
          /cklabScheduler/ s|http://127.0.0.1:5080/[[:space:]]*$|http://127.0.0.1:5080/cklabScheduler/|
        Only lines that contain /cklabScheduler/ are eligible for substitution.
        """
        def _fix_line(line):
            if "/cklabScheduler/" in line:
                return re.sub(
                    r"http://127\.0\.0\.1:5080/\s*$",
                    "http://127.0.0.1:5080/cklabScheduler/",
                    line,
                )
            return line

        return "".join(_fix_line(line) for line in text.splitlines(keepends=True))

    def test_r2_pattern_detected(self):
        assert self._is_r2(self.R2_CONF), "r2 config must be detected as r2"

    def test_r2_pattern_not_false_positive_on_r3(self):
        assert not self._is_r2(self.R3_CONF), "r3 config must not be detected as r2"

    def test_r2_pattern_not_false_positive_on_custom(self):
        assert not self._is_r2(self.CUSTOM_CONF), "custom config must not be detected as r2"

    def test_r3_pattern_detected(self):
        assert self._is_r3(self.R3_CONF), "r3 config must be detected as r3"

    def test_r3_pattern_not_false_positive_on_r2(self):
        assert not self._is_r3(self.R2_CONF), "r2 config must not be detected as r3"

    def test_r3_pattern_not_false_positive_on_custom(self):
        assert not self._is_r3(self.CUSTOM_CONF), "custom config must not be detected as r3"

    def test_migration_fixes_r2_proxypass(self):
        migrated = self._apply_migration(self.R2_CONF)
        assert self._is_r3(migrated), "migrated r2 config must satisfy r3 detection"
        assert not self._is_r2(migrated), "migrated config must no longer satisfy r2 detection"

    def test_migration_fixes_both_proxy_lines(self):
        migrated = self._apply_migration(self.R2_CONF)
        assert migrated.count("http://127.0.0.1:5080/cklabScheduler/") == 2, \
            "Both ProxyPass and ProxyPassReverse lines must be updated"

    def test_migration_is_idempotent_on_r3(self):
        migrated = self._apply_migration(self.R3_CONF)
        assert migrated == self.R3_CONF, \
            "Applying migration to an r3 config must produce no change (idempotent)"

    def test_migration_does_not_alter_custom_config(self):
        migrated = self._apply_migration(self.CUSTOM_CONF)
        assert migrated == self.CUSTOM_CONF, \
            "Migration must not alter a custom (non-localhost) ProxyPass target"
