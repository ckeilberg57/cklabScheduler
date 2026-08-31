"""
Regression tests for Pexip endpoint discovery and the /api/endpoints route.

Root cause fixed (r4):
  app/pexip.py list_registered_endpoints() was collecting is_registered from
  the Pexip status API but never filtering on it.  Newer Pexip firmware
  returns ALL configured registration aliases with their current is_registered
  status (True = connected, False = offline/unconfigured).  The result was
  that aliases configured in Pexip but not currently connected — such as a
  device that is powered off — appeared in the scheduler UI.

  Fix: skip any alias where is_registered is explicitly False.
  Backward compat: if the field is absent entirely (older firmware that only
  emits currently-registered aliases), the alias is included.
"""
import pytest
import requests
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.pexip import PexipAPI


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pexip():
    """PexipAPI instance with Settings stubbed so __init__ doesn't need env."""
    with patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST",    "edge.example.com"), \
         patch.object(Settings, "API_USER",        "user"), \
         patch.object(Settings, "API_PASS",        "pass"):
        return PexipAPI()


def _pexip_page(items):
    """Wrap a list of alias objects in the Pexip paginated response envelope."""
    return {"objects": items}


def make_app(test_db):
    from unittest.mock import MagicMock
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = []
    with patch.object(Settings, "DB_PATH",          test_db), \
         patch.object(Settings, "REG_STATUS_HOST",  "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST",     "edge.example.com"), \
         patch.object(Settings, "API_USER",         "user"), \
         patch.object(Settings, "API_PASS",         "pass"), \
         patch.object(Settings, "SECRET_KEY",       "testsecret"), \
         patch.object(Settings, "O365_ENABLED",     False), \
         patch("app.PexipAPI", return_value=mock_pexip):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app, mock_pexip


# ── Unit tests for list_registered_endpoints ──────────────────────────────────

class TestListRegisteredEndpoints:
    """Unit tests for PexipAPI.list_registered_endpoints()."""

    def test_single_registered_endpoint_returned(self):
        """One registered alias → exactly one result."""
        pexip = _make_pexip()
        items = [{"alias": "room@example.com", "display_name": "Boardroom", "is_registered": True}]
        with patch.object(pexip, "_status_request", return_value=_pexip_page(items)):
            result = pexip.list_registered_endpoints()
        assert len(result) == 1
        assert result[0]["alias"] == "room@example.com"
        assert result[0]["display_name"] == "Boardroom"
        assert result[0]["is_registered"] is True

    def test_multiple_registered_endpoints_returned(self):
        """Three registered aliases → three results, no extras."""
        pexip = _make_pexip()
        items = [
            {"alias": "alpha@example.com", "display_name": "Alpha", "is_registered": True},
            {"alias": "beta@example.com",  "display_name": "Beta",  "is_registered": True},
            {"alias": "gamma@example.com", "display_name": "Gamma", "is_registered": True},
        ]
        with patch.object(pexip, "_status_request", return_value=_pexip_page(items)):
            result = pexip.list_registered_endpoints()
        assert len(result) == 3
        aliases = {ep["alias"] for ep in result}
        assert aliases == {"alpha@example.com", "beta@example.com", "gamma@example.com"}

    def test_zero_endpoints_returns_empty_list(self):
        """Pexip returns no aliases → empty list (no fallback or demo data)."""
        pexip = _make_pexip()
        with patch.object(pexip, "_status_request", return_value=_pexip_page([])):
            result = pexip.list_registered_endpoints()
        assert result == []

    def test_unregistered_alias_filtered_out(self):
        """is_registered=False → alias is excluded from results."""
        pexip = _make_pexip()
        items = [
            {"alias": "online@example.com",              "is_registered": True},
            {"alias": "stale-device@example.com", "is_registered": False},
        ]
        with patch.object(pexip, "_status_request", return_value=_pexip_page(items)):
            result = pexip.list_registered_endpoints()
        assert len(result) == 1
        assert result[0]["alias"] == "online@example.com"

    def test_only_registered_aliases_returned_from_mixed_list(self):
        """Mix of registered/unregistered → only registered ones pass through."""
        pexip = _make_pexip()
        items = [
            {"alias": "live-a@example.com",  "is_registered": True},
            {"alias": "dead-b@example.com",  "is_registered": False},
            {"alias": "live-c@example.com",  "is_registered": True},
            {"alias": "dead-d@example.com",  "is_registered": False},
        ]
        with patch.object(pexip, "_status_request", return_value=_pexip_page(items)):
            result = pexip.list_registered_endpoints()
        assert len(result) == 2
        aliases = {ep["alias"] for ep in result}
        assert aliases == {"live-a@example.com", "live-c@example.com"}

    def test_stale_alias_cannot_appear_when_unregistered(self):
        """An alias with is_registered=False must not appear in results."""
        pexip = _make_pexip()
        items = [{"alias": "stale-device@example.com", "is_registered": False}]
        with patch.object(pexip, "_status_request", return_value=_pexip_page(items)):
            result = pexip.list_registered_endpoints()
        assert result == []
        assert all(ep["alias"] != "stale-device@example.com" for ep in result)

    def test_missing_is_registered_field_includes_endpoint(self):
        """Older Pexip firmware omits is_registered; treat absence as registered=True."""
        pexip = _make_pexip()
        items = [{"alias": "legacy@example.com", "display_name": "Legacy room"}]
        with patch.object(pexip, "_status_request", return_value=_pexip_page(items)):
            result = pexip.list_registered_endpoints()
        assert len(result) == 1
        assert result[0]["alias"] == "legacy@example.com"

    def test_registered_false_explicitly_excludes_even_with_no_registered_field(self):
        """registered=False (alternate field name) also excludes the alias."""
        pexip = _make_pexip()
        items = [{"alias": "offline@example.com", "registered": False}]
        with patch.object(pexip, "_status_request", return_value=_pexip_page(items)):
            result = pexip.list_registered_endpoints()
        assert result == []

    def test_pexip_api_error_propagates_not_swallowed(self):
        """When Pexip API raises, the exception propagates — no silent fallback."""
        pexip = _make_pexip()
        with patch.object(pexip, "_status_request",
                          side_effect=requests.HTTPError("403 Forbidden")):
            with pytest.raises(requests.HTTPError):
                pexip.list_registered_endpoints()


# ── Flask API route tests ─────────────────────────────────────────────────────

class TestEndpointsApiRoute:
    """Integration tests for the /api/endpoints Flask route."""

    def test_one_pexip_endpoint_returns_one_item(self, test_db):
        """A single registered Pexip endpoint produces exactly one item in the API."""
        app, mock_pexip = make_app(test_db)
        mock_pexip.list_registered_endpoints.return_value = [
            {"alias": "room@example.com", "display_name": "Boardroom",
             "is_registered": True, "protocol": "sip", "node": ""},
        ]
        with app.test_client() as client:
            resp = client.get("/api/endpoints")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["items"]) == 1
        assert data["items"][0]["alias"] == "room@example.com"

    def test_multiple_pexip_endpoints_returns_same_count(self, test_db):
        """Multiple registered endpoints produce exactly that many items — no extras."""
        app, mock_pexip = make_app(test_db)
        mock_pexip.list_registered_endpoints.return_value = [
            {"alias": "a@example.com", "display_name": "A", "is_registered": True, "protocol": "", "node": ""},
            {"alias": "b@example.com", "display_name": "B", "is_registered": True, "protocol": "", "node": ""},
            {"alias": "c@example.com", "display_name": "C", "is_registered": True, "protocol": "", "node": ""},
        ]
        with app.test_client() as client:
            resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["items"]) == 3

    def test_zero_pexip_endpoints_returns_empty_items_not_fallback(self, test_db):
        """Pexip returning zero registered endpoints → empty items list, no demo data."""
        app, mock_pexip = make_app(test_db)
        mock_pexip.list_registered_endpoints.return_value = []
        with app.test_client() as client:
            resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["ok"] is True
        assert data["items"] == []

    def test_pexip_api_error_returns_error_state_not_fallback(self, test_db):
        """Pexip API failure → error response (ok=False), no fallback/demo endpoints."""
        app, mock_pexip = make_app(test_db)
        mock_pexip.list_registered_endpoints.side_effect = Exception("Connection refused")
        with app.test_client() as client:
            resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert resp.status_code == 500
        assert data["ok"] is False
        assert "Connection refused" in data["error"]
        assert data["items"] == []

    def test_stale_alias_absent_from_api_response(self, test_db):
        """A stale alias cannot appear in the API response unless Pexip
        explicitly reports it as registered in the mocked response."""
        app, mock_pexip = make_app(test_db)
        mock_pexip.list_registered_endpoints.return_value = [
            {"alias": "real@example.com", "display_name": "Real endpoint",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        with app.test_client() as client:
            resp = client.get("/api/endpoints")
        data = resp.get_json()
        aliases = [ep["alias"] for ep in data["items"]]
        assert "stale-device@example.com" not in aliases

    def test_stale_alias_appears_only_when_explicitly_mocked(self, test_db):
        """If Pexip explicitly returns the stale alias as registered, it must appear."""
        app, mock_pexip = make_app(test_db)
        mock_pexip.list_registered_endpoints.return_value = [
            {"alias": "stale-device@example.com", "display_name": "Test device",
             "is_registered": True, "protocol": "", "node": ""},
        ]
        with app.test_client() as client:
            resp = client.get("/api/endpoints")
        data = resp.get_json()
        aliases = [ep["alias"] for ep in data["items"]]
        assert "stale-device@example.com" in aliases
