import pytest
from unittest.mock import patch

from app.config import Settings


def test_validate_web_reports_all_missing():
    with patch.object(Settings, "REG_STATUS_HOST", ""), \
         patch.object(Settings, "COMMAND_HOST", ""), \
         patch.object(Settings, "API_USER", ""), \
         patch.object(Settings, "API_PASS", ""), \
         patch.object(Settings, "SECRET_KEY", ""), \
         patch.object(Settings, "O365_ENABLED", False):
        with pytest.raises(RuntimeError) as exc_info:
            Settings.validate_web()
        msg = str(exc_info.value)
        assert "REG_STATUS_HOST" in msg
        assert "COMMAND_HOST" in msg
        assert "MGMT_USER" in msg
        assert "MGMT_PASS" in msg
        assert "SECRET_KEY" in msg


def test_validate_web_succeeds_when_all_set():
    with patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "API_USER", "user"), \
         patch.object(Settings, "API_PASS", "pass"), \
         patch.object(Settings, "SECRET_KEY", "supersecret"), \
         patch.object(Settings, "O365_ENABLED", False):
        Settings.validate_web()


def test_validate_web_o365_requires_extra_vars_when_enabled():
    with patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "API_USER", "user"), \
         patch.object(Settings, "API_PASS", "pass"), \
         patch.object(Settings, "SECRET_KEY", "supersecret"), \
         patch.object(Settings, "O365_ENABLED", True), \
         patch.object(Settings, "O365_TENANT_ID", ""), \
         patch.object(Settings, "O365_CLIENT_ID", ""), \
         patch.object(Settings, "O365_CLIENT_SECRET", ""), \
         patch.object(Settings, "O365_FROM_MAILBOX", ""):
        with pytest.raises(RuntimeError) as exc_info:
            Settings.validate_web()
        msg = str(exc_info.value)
        assert "O365_TENANT_ID" in msg
        assert "O365_CLIENT_ID" in msg
        assert "O365_CLIENT_SECRET" in msg
        assert "O365_FROM_MAILBOX" in msg


def test_validate_web_o365_not_required_when_disabled():
    with patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "API_USER", "user"), \
         patch.object(Settings, "API_PASS", "pass"), \
         patch.object(Settings, "SECRET_KEY", "supersecret"), \
         patch.object(Settings, "O365_ENABLED", False), \
         patch.object(Settings, "O365_TENANT_ID", ""), \
         patch.object(Settings, "O365_CLIENT_ID", ""):
        Settings.validate_web()


def test_validate_worker_fails_without_command_host():
    with patch.object(Settings, "COMMAND_HOST", ""):
        with pytest.raises(RuntimeError) as exc_info:
            Settings.validate_worker()
        assert "COMMAND_HOST" in str(exc_info.value)


def test_validate_worker_only_needs_command_host():
    with patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "REG_STATUS_HOST", ""), \
         patch.object(Settings, "API_USER", ""), \
         patch.object(Settings, "API_PASS", ""), \
         patch.object(Settings, "SECRET_KEY", ""):
        Settings.validate_worker()


def test_validate_worker_does_not_require_secret_key():
    with patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "SECRET_KEY", ""):
        Settings.validate_worker()
