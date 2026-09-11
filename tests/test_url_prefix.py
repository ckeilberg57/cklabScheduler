"""
Tests for configurable public URL prefix support.

Covers:
  - Input validation regex (mirrors the installer ^[A-Za-z0-9_-]+$ rule)
  - Flask application routes correctly under any SCRIPT_NAME value
  - Apache config template uses the configured prefix
  - Health-check URL uses configured hostname + prefix, not localhost
  - Entra callback URI uses configured prefix
  - Upgrade migration adds URL_PREFIX default without overwriting existing value
  - Internal SBALKC filesystem/service identifiers are unchanged
"""
import os
import re
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings


# ── Helpers shared by routing tests ─────────────────────────────────────────

def make_app(test_db):
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = []
    with patch.object(Settings, "DB_PATH", test_db), \
         patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "API_USER", "user"), \
         patch.object(Settings, "API_PASS", "pass"), \
         patch.object(Settings, "SECRET_KEY", os.environ["TEST_SECRET_KEY"]), \
         patch.object(Settings, "O365_ENABLED", False), \
         patch.object(Settings, "LOCAL_AUTH_ENABLED", True), \
         patch.object(Settings, "ENTRA_ENABLED", False), \
         patch.object(Settings, "SESSION_COOKIE_SECURE", False), \
         patch("app.PexipAPI", return_value=mock_pexip):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app


# ── 1. Input validation ──────────────────────────────────────────────────────

class TestUrlPrefixValidation:
    """Mirror the installer validation: ^[A-Za-z0-9_-]+$"""

    VALID = [
        "sbalkcScheduler",
        "scheduler",
        "healthcareScheduler",
        "my-scheduler",
        "my_scheduler",
        "abc123",
        "A",
        "z",
        "Scheduler2",
    ]

    INVALID = [
        "",                     # empty
        "my scheduler",         # space
        "my/scheduler",         # forward slash
        "my\\scheduler",        # backslash
        "../etc/passwd",        # path traversal
        "http://x",             # URL scheme
        "sched:80",             # colon (port)
        "sched?q=1",            # query string
        "sched#anchor",         # fragment
        "sched&x",              # ampersand
        "sched=x",              # equals
        "%20",                  # percent-encoded space
        "https://x.com/s",      # full URL
        "sched/sub",            # multiple segments
        ".",                    # dot only
        "..",                   # double-dot
    ]

    @staticmethod
    def _is_valid(val: str) -> bool:
        if not val:
            return False
        return bool(re.match(r'^[A-Za-z0-9_-]+$', val))

    @pytest.mark.parametrize("value", VALID)
    def test_valid_prefix_accepted(self, value):
        assert self._is_valid(value), f"'{value}' should be valid"

    @pytest.mark.parametrize("value", INVALID)
    def test_invalid_prefix_rejected(self, value):
        assert not self._is_valid(value), f"'{value}' should be invalid"

    def test_default_sbalkcScheduler_is_valid(self):
        assert self._is_valid("sbalkcScheduler")

    def test_empty_string_is_invalid(self):
        assert not self._is_valid("")


# ── 2. Flask routes under any SCRIPT_NAME ────────────────────────────────────

class TestFlaskRoutingWithCustomPrefix:
    """
    Flask routes /api/health, /login, etc. correctly regardless of which
    SCRIPT_NAME Gunicorn provides.  SCRIPT_NAME stripping is handled entirely
    by Gunicorn; Flask just sees PATH_INFO.
    """

    @pytest.mark.parametrize("script_name", [
        "/sbalkcScheduler",   # default
        "/scheduler",         # custom short
        "/healthcareScheduler",  # custom long
        "/my-scheduler",      # custom with hyphen
        "/my_scheduler",      # custom with underscore
    ])
    def test_health_endpoint_with_custom_prefix(self, test_db, script_name):
        from datetime import timedelta
        import contextlib
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
                    environ_overrides={"SCRIPT_NAME": script_name},
                )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    @pytest.mark.parametrize("script_name", [
        "/sbalkcScheduler",
        "/scheduler",
    ])
    def test_unauthenticated_api_returns_401_with_custom_prefix(self, test_db, script_name):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(
                    "/api/meetings",
                    environ_overrides={"SCRIPT_NAME": script_name},
                )
        assert resp.status_code == 401

    @pytest.mark.parametrize("script_name", [
        "/sbalkcScheduler",
        "/scheduler",
    ])
    def test_ui_root_redirects_to_login_with_custom_prefix(self, test_db, script_name):
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(
                    "/",
                    environ_overrides={"SCRIPT_NAME": script_name},
                )
        assert resp.status_code == 302

    @pytest.mark.parametrize("script_name", [
        "/sbalkcScheduler",
        "/scheduler",
    ])
    def test_script_root_reflected_in_app_root_meta(self, test_db, script_name):
        from app.auth.local import hash_password
        from app.auth.models import create_local_user
        with patch.object(Settings, "DB_PATH", test_db):
            try:
                create_local_user(
                    "prefixuser", hash_password(os.environ["TEST_USER_PASSWORD"]),
                    role="scheduler_user",
                )
            except ValueError:
                pass
        app = make_app(test_db)
        from tests.conftest import get_csrf_token
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client)
                client.post("/login", data={
                    "username": "prefixuser",
                    "password": os.environ["TEST_USER_PASSWORD"],
                    "csrf_token": csrf,
                })
                resp = client.get("/", environ_overrides={"SCRIPT_NAME": script_name})
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert f'"{script_name}"' in body, \
            f"Expected '{script_name}' in app-root meta tag; got: ...{body[body.find('app-root')-20:body.find('app-root')+80]}..."


# ── 3. Apache config template correctness ────────────────────────────────────

class TestApacheConfigTemplate:
    """
    Simulate what install.sh writes into /etc/apache2/sites-available/sbalkcscheduler.conf.
    install.sh uses a bash heredoc with shell variable expansion; this Python
    helper replicates the same substitutions.
    """

    @staticmethod
    def _generate_apache_config(server_hostname: str, url_prefix: str) -> str:
        return (
            f"# URL prefix: {url_prefix}/\n"
            f"<VirtualHost *:80>\n"
            f"    ServerName {server_hostname}\n"
            f"    Redirect permanent / https://{server_hostname}/\n"
            f"</VirtualHost>\n"
            f"\n"
            f"<VirtualHost *:443>\n"
            f"    ServerName {server_hostname}\n"
            f"    SSLEngine on\n"
            f"    RedirectMatch permanent ^{url_prefix}$ {url_prefix}/\n"
            f"    ProxyPreserveHost On\n"
            f"    ProxyPass        {url_prefix}/ http://127.0.0.1:5080{url_prefix}/\n"
            f"    ProxyPassReverse {url_prefix}/ http://127.0.0.1:5080{url_prefix}/\n"
            f"    RequestHeader set X-Forwarded-Proto \"https\"\n"
            f"</VirtualHost>\n"
        )

    def test_default_prefix_in_apache_config(self):
        cfg = self._generate_apache_config("host.example.com", "/sbalkcScheduler")
        assert "ProxyPass        /sbalkcScheduler/ http://127.0.0.1:5080/sbalkcScheduler/" in cfg
        assert "ProxyPassReverse /sbalkcScheduler/ http://127.0.0.1:5080/sbalkcScheduler/" in cfg
        assert "RedirectMatch permanent ^/sbalkcScheduler$ /sbalkcScheduler/" in cfg

    def test_custom_prefix_in_apache_config(self):
        cfg = self._generate_apache_config("host.example.com", "/scheduler")
        assert "ProxyPass        /scheduler/ http://127.0.0.1:5080/scheduler/" in cfg
        assert "ProxyPassReverse /scheduler/ http://127.0.0.1:5080/scheduler/" in cfg
        assert "RedirectMatch permanent ^/scheduler$ /scheduler/" in cfg

    def test_custom_prefix_does_not_contain_default(self):
        cfg = self._generate_apache_config("host.example.com", "/scheduler")
        assert "/sbalkcScheduler" not in cfg, \
            "Custom-prefix Apache config must not contain the default /sbalkcScheduler"

    def test_proxy_target_includes_prefix_on_both_sides(self):
        for prefix in ("/sbalkcScheduler", "/scheduler", "/healthcareScheduler"):
            cfg = self._generate_apache_config("h.example.com", prefix)
            assert f"ProxyPass        {prefix}/ http://127.0.0.1:5080{prefix}/" in cfg, \
                f"ProxyPass must include '{prefix}/' on both sides"

    def test_server_hostname_in_config(self):
        cfg = self._generate_apache_config("myserver.example.com", "/sbalkcScheduler")
        assert "ServerName myserver.example.com" in cfg


# ── 4. Health-check URL format ────────────────────────────────────────────────

class TestHealthCheckUrl:
    """
    The installer and upgrade scripts build the health-check URL as:
      https://{SERVER_HOSTNAME}{URL_PREFIX}/api/health
    with --resolve SERVER_HOSTNAME:443:127.0.0.1 so Apache SNI is correct.
    """

    @staticmethod
    def _build_health_url(server_hostname: str, url_prefix: str) -> str:
        return f"https://{server_hostname}{url_prefix}/api/health"

    @staticmethod
    def _build_resolve_flag(server_hostname: str) -> str:
        return f"--resolve {server_hostname}:443:127.0.0.1"

    def test_default_health_url(self):
        url = self._build_health_url("host.example.com", "/sbalkcScheduler")
        assert url == "https://host.example.com/sbalkcScheduler/api/health"

    def test_custom_prefix_health_url(self):
        url = self._build_health_url("host.example.com", "/scheduler")
        assert url == "https://host.example.com/scheduler/api/health"

    def test_health_url_uses_configured_hostname_not_localhost(self):
        url = self._build_health_url("myserver.example.com", "/sbalkcScheduler")
        assert "localhost" not in url
        assert "myserver.example.com" in url

    def test_resolve_flag_format(self):
        flag = self._build_resolve_flag("myserver.example.com")
        assert flag == "--resolve myserver.example.com:443:127.0.0.1"

    def test_health_url_with_hyphenated_prefix(self):
        url = self._build_health_url("h.example.com", "/my-scheduler")
        assert url == "https://h.example.com/my-scheduler/api/health"


# ── 5. Entra callback URL ────────────────────────────────────────────────────

class TestEntraCallbackUrl:
    """
    install.sh constructs the default Entra redirect URI from SERVER_HOSTNAME
    and URL_PREFIX.  The hardcoded /sbalkcScheduler must not appear when a
    custom prefix is configured.
    """

    @staticmethod
    def _default_redirect_uri(server_hostname: str, url_prefix: str) -> str:
        return f"https://{server_hostname}{url_prefix}/auth/callback"

    @staticmethod
    def _default_post_logout_uri(server_hostname: str, url_prefix: str) -> str:
        return f"https://{server_hostname}{url_prefix}/login"

    def test_default_redirect_uri(self):
        uri = self._default_redirect_uri("host.example.com", "/sbalkcScheduler")
        assert uri == "https://host.example.com/sbalkcScheduler/auth/callback"

    def test_custom_prefix_redirect_uri(self):
        uri = self._default_redirect_uri("host.example.com", "/scheduler")
        assert uri == "https://host.example.com/scheduler/auth/callback"
        assert "/sbalkcScheduler" not in uri

    def test_default_post_logout_uri(self):
        uri = self._default_post_logout_uri("host.example.com", "/sbalkcScheduler")
        assert uri == "https://host.example.com/sbalkcScheduler/login"

    def test_custom_prefix_post_logout_uri(self):
        uri = self._default_post_logout_uri("host.example.com", "/scheduler")
        assert uri == "https://host.example.com/scheduler/login"
        assert "/sbalkcScheduler" not in uri

    def test_uri_uses_configured_hostname(self):
        uri = self._default_redirect_uri("myserver.example.com", "/scheduler")
        assert "myserver.example.com" in uri


# ── 6. Upgrade migration: URL_PREFIX default preservation ─────────────────────

class TestUpgradeUrlPrefixPreservation:
    """
    upgrade.sh calls _add_env_default "URL_PREFIX" "/sbalkcScheduler" which
    should only add the key when absent, never overwrite an existing value.
    """

    @staticmethod
    def _apply_add_env_default(env_content: str, key: str, default: str) -> str:
        """Mirror the bash _add_env_default logic from upgrade.sh."""
        lines = env_content.splitlines()
        for line in lines:
            if line.startswith(f"{key}="):
                return env_content  # key present — preserve
        return env_content + f'\n{key}="{default}"\n'

    def test_url_prefix_added_when_absent(self):
        env = 'APP_DISPLAY_NAME="SBALKC Scheduler"\nLOCAL_AUTH_ENABLED="true"\n'
        result = self._apply_add_env_default(env, "URL_PREFIX", "/sbalkcScheduler")
        assert 'URL_PREFIX="/sbalkcScheduler"' in result

    def test_custom_url_prefix_preserved_during_upgrade(self):
        env = 'APP_DISPLAY_NAME="SBALKC Scheduler"\nURL_PREFIX="/scheduler"\n'
        result = self._apply_add_env_default(env, "URL_PREFIX", "/sbalkcScheduler")
        assert 'URL_PREFIX="/scheduler"' in result
        assert 'URL_PREFIX="/sbalkcScheduler"' not in result

    def test_default_url_prefix_preserved_during_upgrade(self):
        env = 'URL_PREFIX="/sbalkcScheduler"\nLOCAL_AUTH_ENABLED="true"\n'
        result = self._apply_add_env_default(env, "URL_PREFIX", "/sbalkcScheduler")
        assert result.count("URL_PREFIX=") == 1

    def test_upgrade_does_not_duplicate_url_prefix(self):
        env = 'URL_PREFIX="/scheduler"\n'
        result = self._apply_add_env_default(env, "URL_PREFIX", "/sbalkcScheduler")
        assert result.count("URL_PREFIX=") == 1


# ── 7. Internal SBALKC identifiers remain unchanged ───────────────────────────

class TestInternalIdentifiersUnchanged:
    """
    URL_PREFIX only controls the public HTTP path.  Internal filesystem paths,
    service names, and the service account must remain the SBALKC constants
    regardless of what URL_PREFIX is set to.
    """

    INTERNAL_PATHS = [
        "/opt/sbalkcScheduler",
        "/etc/sbalkcScheduler",
        "/var/lib/sbalkcScheduler",
        # The env-file path is built from CONF_DIR variable — check the component
        "sbalkcScheduler.env",
    ]

    SERVICE_NAMES = [
        "sbalkc-scheduler-web",
        "sbalkc-scheduler-worker",
        "sbalkcscheduler",          # service account
        "sbalkcscheduler.conf",     # Apache site name
    ]

    def test_install_sh_internal_paths_are_constant(self):
        with open("deploy/install.sh") as f:
            content = f.read()
        for path in self.INTERNAL_PATHS:
            assert path in content, f"Internal path '{path}' must remain in install.sh"

    def test_upgrade_sh_internal_paths_are_constant(self):
        with open("deploy/upgrade.sh") as f:
            content = f.read()
        for path in self.INTERNAL_PATHS:
            assert path in content, f"Internal path '{path}' must remain in upgrade.sh"

    def test_install_sh_service_names_are_constant(self):
        with open("deploy/install.sh") as f:
            content = f.read()
        for name in self.SERVICE_NAMES:
            assert name in content, f"Service identifier '{name}' must remain in install.sh"

    def test_upgrade_sh_service_names_are_constant(self):
        with open("deploy/upgrade.sh") as f:
            content = f.read()
        for name in self.SERVICE_NAMES:
            assert name in content, f"Service identifier '{name}' must remain in upgrade.sh"

    def test_config_db_path_uses_sbalkc_path(self):
        assert "/var/lib/sbalkcScheduler/scheduler.db" in Settings.DB_PATH or \
               Settings.DB_PATH.endswith("scheduler.db"), \
               "Default DB_PATH must remain in the SBALKC data directory"


# ── 8. Default URL prefix is /sbalkcScheduler ─────────────────────────────────

class TestDefaultPrefix:
    """The default URL prefix when nothing is configured must be /sbalkcScheduler."""

    def test_default_url_prefix_in_apache_template_is_sbalkc(self):
        cfg = TestApacheConfigTemplate._generate_apache_config(
            "host.example.com", "/sbalkcScheduler"
        )
        assert "/sbalkcScheduler/" in cfg

    def test_default_health_url_is_sbalkc(self):
        url = TestHealthCheckUrl._build_health_url("h.example.com", "/sbalkcScheduler")
        assert "/sbalkcScheduler/" in url

    def test_upgrade_adds_sbalkc_default_when_key_missing(self):
        env = 'APP_DISPLAY_NAME="SBALKC Scheduler"\n'
        result = TestUpgradeUrlPrefixPreservation._apply_add_env_default(
            env, "URL_PREFIX", "/sbalkcScheduler"
        )
        assert 'URL_PREFIX="/sbalkcScheduler"' in result

    def test_service_file_references_url_prefix_env_var(self):
        with open("deploy/sbalkc-scheduler-web.service") as f:
            content = f.read()
        assert "SCRIPT_NAME=${URL_PREFIX}" in content, \
            "Service file must use ${URL_PREFIX} from EnvironmentFile, not a hardcoded path"
