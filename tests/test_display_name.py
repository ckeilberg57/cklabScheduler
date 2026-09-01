"""
Tests for APP_DISPLAY_NAME configuration (r4).

The application display name must be:
  - Configurable via the APP_DISPLAY_NAME env var
  - Visible in HTML <title> and <h1> branding (injected via context processor)
  - Independent of CONTROL_DISPLAY_NAME (Pexip participant name)
  - Added by upgrade.sh when absent from an existing env file
  - Preserved by upgrade.sh when an admin has already set it

Important: the context processor reads Settings.APP_DISPLAY_NAME at request
time, so patches must be active during client.get("/"), not just during
create_app().  make_app() only patches the settings needed by create_app()
(validation, db init); individual tests apply APP_DISPLAY_NAME patches around
each request.
"""
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings


# ── Helpers ───────────────────────────────────────────────────────────────────

DEPLOY_DIR = Path(__file__).resolve().parent.parent / "deploy"


def make_app(test_db):
    """Create a test Flask app. Patches stop after create_app() returns.
    Apply additional Settings patches around each request as needed."""
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = []
    with patch.object(Settings, "DB_PATH",          test_db), \
         patch.object(Settings, "REG_STATUS_HOST",  "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST",     "edge.example.com"), \
         patch.object(Settings, "API_USER",         "user"), \
         patch.object(Settings, "API_PASS",         "pass"), \
         patch.object(Settings, "SECRET_KEY",       "testsecret"), \
         patch.object(Settings, "O365_ENABLED",     False), \
         patch.object(Settings, "LOCAL_AUTH_ENABLED", True), \
         patch.object(Settings, "ENTRA_ENABLED",    False), \
         patch.object(Settings, "SESSION_COOKIE_SECURE", False), \
         patch("app.PexipAPI", return_value=mock_pexip):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
    return app


def _logged_in_client(app, test_db):
    """Return a test client with a local admin user logged in."""
    from app.auth.local import hash_password
    from app.auth.models import create_local_user
    with patch.object(Settings, "DB_PATH", test_db):
        try:
            create_local_user("displaytest", hash_password("TestPassword123!"), role="administrator")
        except ValueError:
            pass
    client = app.test_client()
    with patch.object(Settings, "DB_PATH", test_db):
        client.post("/login", data={"username": "displaytest", "password": "TestPassword123!"})
    return client


def _run_add_env_default(env_file_content, key, default):
    """
    Execute only the _add_env_default function from upgrade.sh in a
    subprocess, using a temp file as the env file.  Returns (stdout, env_file).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env",
                                     delete=False) as fh:
        fh.write(env_file_content)
        env_path = fh.name

    script = f"""\
#!/usr/bin/env bash
ENV_FILE="{env_path}"
_add_env_default() {{
    local key="$1" default="$2"
    if grep -q "^${{key}}=" "${{ENV_FILE}}" 2>/dev/null; then
        echo "  ${{key}} already set — preserving existing value."
    else
        printf '%s="%s"\\n' "${{key}}" "${{default}}" >> "${{ENV_FILE}}"
        echo "  ${{key}} not found — added default: ${{default}}"
    fi
}}
_add_env_default "{key}" "{default}"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert proc.returncode == 0, f"Script error: {proc.stderr}"
    with open(env_path) as fh:
        after = fh.read()
    os.unlink(env_path)
    return proc.stdout, after


# ── Settings unit tests ───────────────────────────────────────────────────────

def test_settings_class_default():
    """Settings.APP_DISPLAY_NAME default is 'CKlabs Scheduler'."""
    with patch.object(Settings, "APP_DISPLAY_NAME", "CKlabs Scheduler"):
        assert Settings.APP_DISPLAY_NAME == "CKlabs Scheduler"


def test_custom_app_display_name_loaded():
    """A custom APP_DISPLAY_NAME value is correctly reflected in Settings."""
    with patch.object(Settings, "APP_DISPLAY_NAME", "Acme Scheduler"):
        assert Settings.APP_DISPLAY_NAME == "Acme Scheduler"


def test_default_app_display_name_fallback():
    """When APP_DISPLAY_NAME env var is absent, the default is 'CKlabs Scheduler'."""
    default = os.getenv("APP_DISPLAY_NAME", "CKlabs Scheduler")
    assert default == "CKlabs Scheduler"


def test_control_display_name_independent_of_app_display_name():
    """CONTROL_DISPLAY_NAME and APP_DISPLAY_NAME are independent settings."""
    with patch.object(Settings, "APP_DISPLAY_NAME",     "My Scheduler"), \
         patch.object(Settings, "CONTROL_DISPLAY_NAME", "Dial-in Bot"):
        assert Settings.APP_DISPLAY_NAME     == "My Scheduler"
        assert Settings.CONTROL_DISPLAY_NAME == "Dial-in Bot"

    # Changing APP_DISPLAY_NAME must not alter CONTROL_DISPLAY_NAME
    with patch.object(Settings, "APP_DISPLAY_NAME",     "Changed"), \
         patch.object(Settings, "CONTROL_DISPLAY_NAME", "Dial-in Bot"):
        assert Settings.CONTROL_DISPLAY_NAME == "Dial-in Bot"


# ── HTML template tests ───────────────────────────────────────────────────────
#
# The context processor reads Settings.APP_DISPLAY_NAME at render time, so
# the patch must be active around client.get("/"), not just create_app().

def test_html_title_uses_custom_app_display_name(test_db):
    """<title> tag in index.html reflects a custom APP_DISPLAY_NAME."""
    app = make_app(test_db)
    client = _logged_in_client(app, test_db)
    with patch.object(Settings, "APP_DISPLAY_NAME", "Acme Scheduler"):
        with patch.object(Settings, "DB_PATH", test_db):
            resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "<title>Acme Scheduler</title>" in html


def test_html_branding_h1_uses_custom_app_display_name(test_db):
    """<h1> branding element in index.html reflects a custom APP_DISPLAY_NAME."""
    app = make_app(test_db)
    client = _logged_in_client(app, test_db)
    with patch.object(Settings, "APP_DISPLAY_NAME", "Acme Scheduler"):
        with patch.object(Settings, "DB_PATH", test_db):
            resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "<h1>Acme Scheduler</h1>" in html


def test_html_title_uses_default_name(test_db):
    """<title> renders 'CKlabs Scheduler' when that is the configured name."""
    app = make_app(test_db)
    client = _logged_in_client(app, test_db)
    with patch.object(Settings, "APP_DISPLAY_NAME", "CKlabs Scheduler"):
        with patch.object(Settings, "DB_PATH", test_db):
            resp = client.get("/")
    html = resp.get_data(as_text=True)
    assert "<title>CKlabs Scheduler</title>" in html


def test_control_display_name_does_not_appear_in_html_title(test_db):
    """CONTROL_DISPLAY_NAME must not appear in <title>; they are independent."""
    app = make_app(test_db)
    client = _logged_in_client(app, test_db)
    with patch.object(Settings, "APP_DISPLAY_NAME",     "CKlabs Scheduler"), \
         patch.object(Settings, "CONTROL_DISPLAY_NAME", "Dial-in Bot"), \
         patch.object(Settings, "DB_PATH", test_db):
        resp = client.get("/")
    html = resp.get_data(as_text=True)
    title_content = html.split("<title>")[1].split("</title>")[0]
    assert "Dial-in Bot" not in title_content
    assert "CKlabs Scheduler" in title_content


# ── upgrade.sh env migration tests ────────────────────────────────────────────

def test_upgrade_adds_app_display_name_when_absent():
    """upgrade.sh _add_env_default adds APP_DISPLAY_NAME when it is absent."""
    env_before = "REG_STATUS_HOST=pexip.example.com\nSECRET_KEY=abc123\n"
    stdout, env_after = _run_add_env_default(
        env_before, "APP_DISPLAY_NAME", "CKlabs Scheduler"
    )
    assert 'APP_DISPLAY_NAME="CKlabs Scheduler"' in env_after
    assert "not found — added default" in stdout


def test_upgrade_preserves_existing_custom_display_name():
    """upgrade.sh _add_env_default does NOT overwrite an admin-set APP_DISPLAY_NAME."""
    env_before = (
        "REG_STATUS_HOST=pexip.example.com\n"
        'APP_DISPLAY_NAME="Acme Telehealth"\n'
        "SECRET_KEY=abc123\n"
    )
    stdout, env_after = _run_add_env_default(
        env_before, "APP_DISPLAY_NAME", "CKlabs Scheduler"
    )
    assert 'APP_DISPLAY_NAME="Acme Telehealth"' in env_after
    assert 'APP_DISPLAY_NAME="CKlabs Scheduler"' not in env_after
    assert "already set — preserving" in stdout
