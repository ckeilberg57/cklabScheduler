"""
Security regression tests for cklabScheduler.

Covers:
  - Open redirect: _safe_redirect_url rejects javascript:, data:, //host, external URLs.
  - CSRF: state-mutating endpoints reject requests without a valid CSRF token.
  - Security headers: HTML responses include the required protective headers.
  - Hardcoded credentials: .env.example contains no CHANGE_ME placeholder values.
"""
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings


# ── Shared test app factory ──────────────────────────────────────────────────

def make_app(test_db, extra_config=None):
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


# ── Open redirect ─────────────────────────────────────────────────────────────

class TestOpenRedirectHelper:
    """Direct unit tests for _safe_redirect_url within a Flask request context."""

    def _call(self, app, target):
        from app.routes.auth import _safe_redirect_url
        with app.test_request_context("/login"):
            return _safe_redirect_url(target)

    def test_javascript_url_rejected(self, test_db):
        assert self._call(make_app(test_db), "javascript:alert(1)") is None

    def test_javascript_url_mixed_case_rejected(self, test_db):
        assert self._call(make_app(test_db), "JavaScript:alert(1)") is None

    def test_data_url_rejected(self, test_db):
        assert self._call(make_app(test_db), "data:text/html,<script>alert(1)</script>") is None

    def test_protocol_relative_rejected(self, test_db):
        assert self._call(make_app(test_db), "//evil.example.com/steal") is None

    def test_external_https_rejected(self, test_db):
        assert self._call(make_app(test_db), "https://evil.com/phish") is None

    def test_external_http_rejected(self, test_db):
        assert self._call(make_app(test_db), "http://evil.com/phish") is None

    def test_none_returns_none(self, test_db):
        assert self._call(make_app(test_db), None) is None

    def test_empty_string_returns_none(self, test_db):
        assert self._call(make_app(test_db), "") is None

    def test_relative_root_allowed(self, test_db):
        assert self._call(make_app(test_db), "/") == "/"

    def test_relative_api_path_allowed(self, test_db):
        assert self._call(make_app(test_db), "/api/meetings") == "/api/meetings"

    def test_login_redirect_via_next_param_rejects_javascript(self, test_db):
        """End-to-end: login with next=javascript:... must not redirect to it."""
        from app.auth.local import hash_password
        from app.auth.models import create_local_user
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_local_user("redir1", hash_password("TestPassword123!"), role="scheduler_user")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    "/login",
                    data={
                        "username": "redir1",
                        "password": "TestPassword123!",
                        "next": "javascript:alert(1)",
                    },
                    follow_redirects=False,
                )
        location = resp.headers.get("Location", "")
        assert location.startswith("javascript:") is False
        assert "javascript" not in location.lower()

    def test_login_redirect_via_next_param_rejects_external(self, test_db):
        from app.auth.local import hash_password
        from app.auth.models import create_local_user
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_local_user("redir2", hash_password("TestPassword123!"), role="scheduler_user")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.post(
                    "/login",
                    data={
                        "username": "redir2",
                        "password": "TestPassword123!",
                        "next": "//evil.example.com",
                    },
                    follow_redirects=False,
                )
        location = resp.headers.get("Location", "")
        assert "evil.example.com" not in location

    def test_percent_encoded_scheme_relative_rejected(self, test_db):
        """%2F%2Fevil.example must not pass the open redirect check."""
        assert self._call(make_app(test_db), "%2F%2Fevil.example") is None

    def test_percent_encoded_backslash_rejected(self, test_db):
        """%5C%5Cevil.example must not pass the open redirect check."""
        assert self._call(make_app(test_db), "%5C%5Cevil.example") is None

    def test_slash_backslash_rejected(self, test_db):
        """/\\evil.example must not pass the open redirect check."""
        assert self._call(make_app(test_db), "/\\evil.example") is None

    def test_valid_next_stored_in_session_and_used_after_login(self, test_db):
        """GET /login?next=/dashboard stores it; POST redirects there."""
        from app.auth.local import hash_password
        from app.auth.models import create_local_user
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_local_user("redir3", hash_password("TestPassword123!"), role="scheduler_user")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                client.get("/login?next=/dashboard")
                resp = client.post(
                    "/login",
                    data={"username": "redir3", "password": "TestPassword123!"},
                    follow_redirects=False,
                )
        location = resp.headers.get("Location", "")
        assert "/dashboard" in location

    def test_invalid_next_not_stored_falls_back_to_index(self, test_db):
        """GET /login?next=//evil stores nothing; POST redirects to index."""
        from app.auth.local import hash_password
        from app.auth.models import create_local_user
        app = make_app(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            create_local_user("redir4", hash_password("TestPassword123!"), role="scheduler_user")
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                client.get("/login?next=//evil.example.com")
                resp = client.post(
                    "/login",
                    data={"username": "redir4", "password": "TestPassword123!"},
                    follow_redirects=False,
                )
        location = resp.headers.get("Location", "")
        assert "evil.example.com" not in location


# ── CSRF protection ───────────────────────────────────────────────────────────

class TestCSRFProtection:
    """State-mutating endpoints must reject requests without a valid CSRF token."""

    def _csrf_app(self, test_db):
        return make_app(test_db, extra_config={"WTF_CSRF_ENABLED": True})

    def _login(self, client, test_db, app):
        app.config["WTF_CSRF_ENABLED"] = False
        with patch.object(Settings, "DB_PATH", test_db):
            from app.auth.local import hash_password
            from app.auth.models import create_local_user
            try:
                create_local_user("csrfuser", hash_password("TestPassword123!"), role="scheduler_user")
            except ValueError:
                pass
            client.post("/login", data={"username": "csrfuser", "password": "TestPassword123!"})
        app.config["WTF_CSRF_ENABLED"] = True

    def test_create_meeting_without_csrf_returns_400(self, test_db):
        app = self._csrf_app(test_db)
        with app.test_client() as client:
            self._login(client, test_db, app)
            resp = client.post("/api/meetings", json={})
        assert resp.status_code == 400

    def test_delete_meeting_without_csrf_returns_400(self, test_db):
        app = self._csrf_app(test_db)
        with app.test_client() as client:
            self._login(client, test_db, app)
            resp = client.post("/api/meetings/999/delete", json={})
        assert resp.status_code == 400

    def test_extend_meeting_without_csrf_returns_400(self, test_db):
        app = self._csrf_app(test_db)
        with app.test_client() as client:
            self._login(client, test_db, app)
            resp = client.post("/api/meetings/999/extend", json={})
        assert resp.status_code == 400


# ── Security headers ──────────────────────────────────────────────────────────

class TestSecurityHeaders:
    """HTTP responses must include protective headers."""

    def test_x_content_type_options_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_permissions_policy_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        pp = resp.headers.get("Permissions-Policy", "")
        assert "camera=()" in pp
        assert "microphone=()" in pp

    def test_csp_script_src_self_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "script-src 'self'" in csp

    def test_csp_frame_ancestors_none_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "frame-ancestors 'none'" in csp

    def test_csp_base_uri_self_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "base-uri 'self'" in csp

    def test_csp_form_action_self_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "form-action 'self'" in csp

    def test_cache_control_no_store_on_html(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/login")
        cc = resp.headers.get("Cache-Control", "")
        assert "no-store" in cc

    def test_x_content_type_options_on_json(self, test_db):
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/health")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_no_csp_on_json_api(self, test_db):
        """CSP is applied only to HTML responses, not JSON API responses."""
        app = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/health")
        assert "Content-Security-Policy" not in resp.headers


# ── Hardcoded credentials ────────────────────────────────────────────────────

class TestHardcodedCredentials:
    """No plaintext credential placeholders must remain in tracked source files."""

    _REPO = pathlib.Path(__file__).parent.parent

    def test_env_example_has_no_change_me_assignments(self):
        """Every non-comment assignment in .env.example must not use CHANGE_ME."""
        env_example = self._REPO / ".env.example"
        for line in env_example.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert "CHANGE_ME" not in stripped, (
                f".env.example still assigns CHANGE_ME: {stripped!r}"
            )

    def test_python_source_has_no_change_me(self):
        """Python source files must not contain the CHANGE_ME placeholder."""
        for py_file in (self._REPO / "app").rglob("*.py"):
            content = py_file.read_text(errors="replace")
            assert "CHANGE_ME" not in content, (
                f"CHANGE_ME found in {py_file.relative_to(self._REPO)}"
            )


# ── getErrorText / exception rendering ───────────────────────────────────────

class TestErrorTextExtraction:
    """getErrorText must produce a plain string regardless of input type.

    These tests run inside a Node.js-less environment so they exercise the
    Python-side structural guarantees: the source must not route exception data
    through innerHTML, outerHTML, insertAdjacentHTML, or any HTML parser sink.
    The JS-level behaviour is validated by asserting on the static source text.
    """

    _JS = (pathlib.Path(__file__).parent.parent / "app" / "static" / "app.js").read_text()

    # ── Static source-level assertions ───────────────────────────────────────

    def test_no_innerhtml_sink_in_js(self):
        """Production JS must contain no innerHTML assignments."""
        assert ".innerHTML" not in self._JS

    def test_no_outerhtml_sink_in_js(self):
        assert ".outerHTML" not in self._JS

    def test_no_insertadjacenthtml_sink_in_js(self):
        assert "insertAdjacentHTML" not in self._JS

    def test_no_document_write_sink_in_js(self):
        assert "document.write" not in self._JS

    def test_no_domparser_sink_in_js(self):
        assert "DOMParser" not in self._JS

    def test_no_safeErrorMessage_remains(self):
        """safeErrorMessage was replaced by getErrorText; must not exist."""
        assert "safeErrorMessage" not in self._JS

    def test_getErrorText_defined(self):
        assert "function getErrorText" in self._JS

    def test_showErrorToast_defined(self):
        assert "function showErrorToast" in self._JS

    @staticmethod
    def _extract_function(source, name):
        """Return the source of the named function (up to its closing brace)."""
        start = source.index(f"function {name}")
        brace_start = source.index("{", start)
        depth = 0
        for i, ch in enumerate(source[brace_start:], brace_start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return source[start:i + 1]
        return source[start:]

    def test_showErrorToast_uses_getErrorText(self):
        """showErrorToast must call getErrorText, not String() or JSON.stringify()."""
        snippet = self._extract_function(self._JS, "showErrorToast")
        assert "getErrorText" in snippet
        assert "String(" not in snippet
        assert "JSON.stringify" not in snippet

    def test_error_path_uses_textContent_not_html(self):
        """The exception rendering path must use textContent, never innerHTML."""
        snippet = self._extract_function(self._JS, "showErrorToast")
        assert "textContent" in snippet
        assert "innerHTML" not in snippet

    def test_xss_payload_script_tag_would_be_text(self):
        """Verify the JS source does not pass XSS payloads through an HTML sink.

        We confirm this by asserting there is no code path that writes
        '<script>' into an innerHTML-style property anywhere in the file.
        Since all text payloads go through textContent (which the browser
        treats as plain text), the strings below can only appear as text nodes.
        """
        html_sinks = [".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write"]
        for sink in html_sinks:
            assert sink not in self._JS, f"HTML sink still present: {sink!r}"

    def test_img_onerror_payload_cannot_reach_html_sink(self):
        """<img src=x onerror=alert(1)> cannot reach an HTML sink in app.js."""
        html_sinks = [".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write"]
        for sink in html_sinks:
            assert sink not in self._JS

    def test_svg_onload_payload_cannot_reach_html_sink(self):
        '"><svg onload=alert(1)> cannot reach an HTML sink in app.js.'
        html_sinks = [".innerHTML", ".outerHTML", "insertAdjacentHTML", "document.write"]
        for sink in html_sinks:
            assert sink not in self._JS
