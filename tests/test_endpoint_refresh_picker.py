"""
Regression tests for the endpoint-refresh → scheduling-picker-refresh flow.

After a successful endpoint import/refresh the frontend calls
refreshSchedulingAvailability(), which re-renders the endpoint list and
recalculates start/end time defaults.  These server-side tests verify the
API contract that makes those frontend behaviours correct:

  1. Successful refresh → API returns ok=True and items (frontend triggers picker refresh).
  2. Selected endpoint still present → API includes it (frontend preserves selection).
  3. Selected endpoint removed → API omits it (frontend clears stale selection).
  4. Failed refresh → API returns ok=False, items=[] (frontend leaves picker alone).
  5. "Now" is always current → API always returns the live endpoint set (no cached stale state).
  6. Sequential refreshes reflect changes (add then remove produces consistent responses).
  7. Refresh after endpoint set becomes empty → empty items, ok=True (picker recalculates to show no endpoints).
"""
import pytest
from unittest.mock import patch, MagicMock

from app.config import Settings


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_app(test_db, mock_endpoints):
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = mock_endpoints
    with patch.object(Settings, "DB_PATH",              test_db), \
         patch.object(Settings, "REG_STATUS_HOST",      "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST",         "edge.example.com"), \
         patch.object(Settings, "API_USER",             "user"), \
         patch.object(Settings, "API_PASS",             "pass"), \
         patch.object(Settings, "SECRET_KEY",           "testsecret"), \
         patch.object(Settings, "O365_ENABLED",         False), \
         patch.object(Settings, "LOCAL_AUTH_ENABLED",   True), \
         patch.object(Settings, "ENTRA_ENABLED",        False), \
         patch.object(Settings, "SESSION_COOKIE_SECURE", False), \
         patch("app.PexipAPI", return_value=mock_pexip):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        return app, mock_pexip


def _login(client, test_db):
    from app.auth.local import hash_password
    from app.auth.models import create_local_user
    with patch.object(Settings, "DB_PATH", test_db):
        try:
            create_local_user("refresh_admin", hash_password("Passw0rd!"), role="administrator")
        except Exception:
            pass
    client.post("/login", data={"username": "refresh_admin", "password": "Passw0rd!"})


def _ep(alias, display_name="Room", registered=True):
    return {"alias": alias, "display_name": display_name,
            "is_registered": registered, "protocol": "sip", "node": ""}


def _fetch(client, test_db):
    with patch.object(Settings, "DB_PATH", test_db):
        return client.get("/api/endpoints")


# ── Test 1: Successful refresh → API returns ok=True + items ──────────────────

class TestRefreshReturnsOkAndItems:
    """
    Frontend requirement 1: successful endpoint refresh causes date/time
    availability refresh.  The frontend gates refreshSchedulingAvailability()
    on a successful (ok=True) API response.
    """

    def test_single_endpoint_refresh_ok(self, test_db):
        app, _ = _make_app(test_db, [_ep("a@example.com")])
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["items"]) == 1

    def test_multiple_endpoints_refresh_ok(self, test_db):
        endpoints = [_ep("a@example.com"), _ep("b@example.com"), _ep("c@example.com")]
        app, _ = _make_app(test_db, endpoints)
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["items"]) == 3


# ── Test 2: Selected endpoint still present → preserved in response ────────────

class TestSelectedEndpointPreservedIfStillPresent:
    """
    Frontend requirement 2: if the currently selected endpoint is still
    returned by Pexip after refresh, the frontend preserves the selection.
    This test verifies that the API includes the endpoint in its response.
    """

    def test_endpoint_present_in_response_after_refresh(self, test_db):
        selected_alias = "boardroom@example.com"
        app, _ = _make_app(test_db, [_ep(selected_alias, "Boardroom")])
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        data = resp.get_json()
        aliases = [ep["alias"] for ep in data["items"]]
        assert selected_alias in aliases

    def test_previously_selected_endpoint_alias_survives_refresh(self, test_db):
        selected = "exec-conf@example.com"
        app, mock_pexip = _make_app(test_db, [_ep(selected)])
        with app.test_client() as client:
            _login(client, test_db)
            # First call: endpoint present
            resp1 = _fetch(client, test_db)
            assert selected in [ep["alias"] for ep in resp1.get_json()["items"]]
            # Simulate second call (same endpoint still registered)
            resp2 = _fetch(client, test_db)
            assert selected in [ep["alias"] for ep in resp2.get_json()["items"]]


# ── Test 3: Removed endpoint → absent from response (frontend clears it) ──────

class TestRemovedEndpointAbsentFromResponse:
    """
    Frontend requirement 3: if an endpoint disappears after refresh, the
    frontend must clear that selection.  This test confirms the API omits
    the removed endpoint so the frontend has no stale alias to display.
    """

    def test_removed_endpoint_not_in_response(self, test_db):
        removed_alias = "old-room@example.com"
        # Pexip no longer returns the old alias
        app, _ = _make_app(test_db, [_ep("new-room@example.com")])
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        data = resp.get_json()
        aliases = [ep["alias"] for ep in data["items"]]
        assert removed_alias not in aliases

    def test_only_currently_registered_endpoints_returned(self, test_db):
        surviving = "still-here@example.com"
        app, _ = _make_app(test_db, [_ep(surviving)])
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        data = resp.get_json()
        aliases = [ep["alias"] for ep in data["items"]]
        assert aliases == [surviving]


# ── Test 4: Failed refresh → API returns ok=False, items=[] ───────────────────

class TestFailedRefreshDoesNotDestroyPickerState:
    """
    Frontend requirement 7: when endpoint refresh fails, the frontend should
    not clear the picker.  The API returns ok=False + items=[], so the
    frontend skips refreshSchedulingAvailability() and shows an error toast
    without touching the existing time selection.
    """

    def test_pexip_failure_returns_ok_false(self, test_db):
        app, mock_pexip = _make_app(test_db, [])
        mock_pexip.list_registered_endpoints.side_effect = Exception("Timeout")
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["ok"] is False
        assert data["items"] == []

    def test_pexip_failure_error_message_present(self, test_db):
        app, mock_pexip = _make_app(test_db, [])
        mock_pexip.list_registered_endpoints.side_effect = Exception("Connection refused")
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        data = resp.get_json()
        assert "Connection refused" in data.get("error", "")


# ── Test 5: API always returns live state (no stale cached result) ─────────────

class TestApiAlwaysReturnsCurrentState:
    """
    Frontend requirement 6: "now" is recalculated during refresh rather than
    relying on a page-load time.  On the server side, this means the API
    must query Pexip on each call — not return a cached snapshot.
    """

    def test_each_call_queries_pexip(self, test_db):
        app, mock_pexip = _make_app(test_db, [_ep("a@example.com")])
        with app.test_client() as client:
            _login(client, test_db)
            _fetch(client, test_db)
            _fetch(client, test_db)
            _fetch(client, test_db)
        # list_registered_endpoints must be called once per request, not cached
        assert mock_pexip.list_registered_endpoints.call_count == 3

    def test_updated_endpoint_list_reflected_on_next_call(self, test_db):
        app, mock_pexip = _make_app(test_db, [_ep("a@example.com")])
        with app.test_client() as client:
            _login(client, test_db)
            resp1 = _fetch(client, test_db)
            assert len(resp1.get_json()["items"]) == 1

            # Simulate Pexip returning a different set on the next call
            mock_pexip.list_registered_endpoints.return_value = [
                _ep("a@example.com"), _ep("b@example.com"),
            ]
            resp2 = _fetch(client, test_db)
            assert len(resp2.get_json()["items"]) == 2


# ── Test 6: Sequential add-then-remove reflects correctly ─────────────────────

class TestSequentialRefreshesReflectChanges:
    """
    Frontend requirement 5/6: after two successive refreshes (one that adds an
    endpoint, one that removes it), the API response must reflect the final state.
    The frontend must not retain stale selections from earlier responses.
    """

    def test_add_then_remove_endpoint_produces_correct_final_state(self, test_db):
        app, mock_pexip = _make_app(test_db, [_ep("a@example.com")])
        with app.test_client() as client:
            _login(client, test_db)

            # Refresh 1: only "a" registered
            resp1 = _fetch(client, test_db)
            assert [ep["alias"] for ep in resp1.get_json()["items"]] == ["a@example.com"]

            # Refresh 2: "a" and "b" both registered
            mock_pexip.list_registered_endpoints.return_value = [
                _ep("a@example.com"), _ep("b@example.com"),
            ]
            resp2 = _fetch(client, test_db)
            aliases2 = {ep["alias"] for ep in resp2.get_json()["items"]}
            assert aliases2 == {"a@example.com", "b@example.com"}

            # Refresh 3: "b" gone again (only "a" remains)
            mock_pexip.list_registered_endpoints.return_value = [_ep("a@example.com")]
            resp3 = _fetch(client, test_db)
            assert [ep["alias"] for ep in resp3.get_json()["items"]] == ["a@example.com"]


# ── Test 7: Refresh with empty endpoint list ───────────────────────────────────

class TestRefreshWithEmptyEndpointList:
    """
    Frontend requirement 5: if all endpoints disappear, the picker should
    recalculate to show an empty endpoint list.  The API must return ok=True
    with an empty items list so the frontend knows the refresh succeeded.
    """

    def test_empty_pexip_response_returns_ok_true_empty_items(self, test_db):
        app, _ = _make_app(test_db, [])
        with app.test_client() as client:
            _login(client, test_db)
            resp = _fetch(client, test_db)
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["ok"] is True
        assert data["items"] == []

    def test_endpoint_count_drops_to_zero_after_all_deregister(self, test_db):
        app, mock_pexip = _make_app(test_db, [_ep("a@example.com")])
        with app.test_client() as client:
            _login(client, test_db)

            resp1 = _fetch(client, test_db)
            assert len(resp1.get_json()["items"]) == 1

            # All endpoints deregister
            mock_pexip.list_registered_endpoints.return_value = []
            resp2 = _fetch(client, test_db)
            assert resp2.get_json()["ok"] is True
            assert resp2.get_json()["items"] == []
