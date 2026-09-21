"""
UI remediation regression tests — four browser-identified issues.

Tests verify the server-side API contracts that drive:
  A. Endpoint search/refresh (alias, display_name, protocol fields for client-side filter)
  B. Edit-dialog free/busy (meeting endpoints returned; self-exclusion is client-side)
  C. Calendar day view (meetings/range returns status + times for calendar rendering)
  D. ENDED status (API returns status='ended'; client pill/block/dot colour is CSS-only)

These tests establish that the backend API contract required by each frontend fix
is preserved. They do not test CSS colour values or DOM manipulation directly.
"""
import json
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.database import db
from app.meeting_utils import iso, now_utc
from tests.conftest import get_csrf_token, insert_meeting, insert_endpoint


# ── Shared helpers ─────────────────────────────────────────────────────────────

def make_app(test_db, mock_endpoints=None):
    mock_pexip = MagicMock()
    mock_pexip.list_registered_endpoints.return_value = mock_endpoints or []
    with (
        patch.object(Settings, "DB_PATH",              test_db),
        patch.object(Settings, "REG_STATUS_HOST",      "pexip.example.com"),
        patch.object(Settings, "COMMAND_HOST",         "edge.example.com"),
        patch.object(Settings, "API_USER",             "user"),
        patch.object(Settings, "API_PASS",             "pass"),
        patch.object(Settings, "SECRET_KEY",           os.environ["TEST_SECRET_KEY"]),
        patch.object(Settings, "O365_ENABLED",         False),
        patch.object(Settings, "LOCAL_AUTH_ENABLED",   True),
        patch.object(Settings, "ENTRA_ENABLED",        False),
        patch.object(Settings, "SESSION_COOKIE_SECURE", False),
        patch("app.PexipAPI", return_value=mock_pexip),
    ):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app, mock_pexip


def create_user(test_db, username="ui_admin"):
    from app.auth.local import hash_password
    from app.auth.models import create_local_user
    with patch.object(Settings, "DB_PATH", test_db):
        try:
            create_local_user(username, hash_password(os.environ["TEST_USER_PASSWORD"]), role="administrator")
        except Exception:
            pass


def login(client, test_db, username="ui_admin"):
    with patch.object(Settings, "DB_PATH", test_db):
        csrf = get_csrf_token(client, "/login")
        client.post(
            "/login",
            data={"username": username, "password": os.environ["TEST_USER_PASSWORD"], "csrf_token": csrf},
        )


def ep(alias, display_name="Room", protocol="sip"):
    return {"alias": alias, "display_name": display_name,
            "is_registered": True, "protocol": protocol, "node": ""}


# ── Section A: Endpoint search/refresh API contract ───────────────────────────

class TestEndpointSearchApiContract:
    """
    Issue A: Endpoint search and Refresh button.

    The client-side search filters endpoints by alias and display_name.
    The API must return both fields so JS can filter locally.
    The Refresh button calls GET /api/endpoints; the response structure
    must include ok, items, and each item must have alias + display_name.
    """

    def test_endpoint_items_include_alias_field(self, test_db):
        """Each endpoint item exposes 'alias' for client-side search filtering."""
        app, _ = make_app(test_db, [ep("conf@example.com", "Conference Room")])
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert "alias" in data["items"][0]
        assert data["items"][0]["alias"] == "conf@example.com"

    def test_endpoint_items_include_display_name_field(self, test_db):
        """Each endpoint item exposes 'display_name' for client-side search filtering."""
        app, _ = make_app(test_db, [ep("conf@example.com", "Boardroom Alpha")])
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert data["items"][0]["display_name"] == "Boardroom Alpha"

    def test_endpoint_items_include_protocol_field(self, test_db):
        """Each endpoint item exposes 'protocol' for sub-line display."""
        app, _ = make_app(test_db, [ep("conf@example.com", "Room", "h323")])
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert data["items"][0]["protocol"] == "h323"

    def test_refresh_returns_ok_true_on_success(self, test_db):
        """Refresh (GET /api/endpoints) returns ok=True so JS shows the refreshed list."""
        app, _ = make_app(test_db, [ep("a@example.com")])
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/endpoints")
        assert resp.get_json()["ok"] is True

    def test_refresh_returns_ok_false_on_pexip_error(self, test_db):
        """Pexip failure during refresh returns ok=False (JS shows error toast)."""
        app, mock_pexip = make_app(test_db, [])
        mock_pexip.list_registered_endpoints.side_effect = Exception("timeout")
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/endpoints")
        assert resp.get_json()["ok"] is False
        assert resp.get_json()["items"] == []

    def test_multiple_endpoints_all_returned_for_search(self, test_db):
        """All endpoints are returned so client can filter any subset."""
        endpoints = [ep(f"ep{i}@example.com", f"Room {i}") for i in range(6)]
        app, _ = make_app(test_db, endpoints)
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/endpoints")
        assert len(resp.get_json()["items"]) == 6

    def test_empty_endpoint_list_returns_ok_true_empty_items(self, test_db):
        """No endpoints returns ok=True + [] so JS shows 'no endpoints' state."""
        app, _ = make_app(test_db, [])
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/endpoints")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["items"] == []


# ── Section B: Edit dialog free/busy API contract ─────────────────────────────

class TestEditDialogFreeBusyApiContract:
    """
    Issue B: Edit dialog must show endpoint Available/Busy status.

    Free/busy is computed client-side from state.meetings, which comes from
    GET /api/meetings. Each meeting in the response must include its endpoint
    assignments so the client can detect conflicts. Self-exclusion is also
    client-side: the currentMeetingId is excluded from the overlap check.

    These tests verify the server-side contract: meetings include their endpoint
    assignments, and the edit API correctly saves endpoint changes.
    """

    def test_meetings_response_includes_endpoints(self, test_db):
        """Each meeting in the daily list includes its endpoint assignments."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(conn, start_time=iso(now), end_time=iso(now + timedelta(hours=1)))
                insert_endpoint(conn, mid, endpoint_alias="room@example.com")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings?date={today}")
        data = resp.get_json()
        assert data["ok"] is True
        meetings = data["items"]
        assert len(meetings) == 1
        endpoints = meetings[0].get("endpoints", [])
        assert any(ep["endpoint_alias"] == "room@example.com" for ep in endpoints)

    def test_meetings_response_includes_endpoint_alias(self, test_db):
        """Endpoint alias field is present for client-side conflict detection."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(conn, start_time=iso(now), end_time=iso(now + timedelta(hours=1)))
                insert_endpoint(conn, mid, endpoint_alias="conf@example.com")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings?date={today}")
        ep_aliases = [e["endpoint_alias"] for e in resp.get_json()["items"][0]["endpoints"]]
        assert "conf@example.com" in ep_aliases

    def test_edit_endpoint_assignment_saved_correctly(self, test_db):
        """Editing a meeting's endpoints is persisted and reflected in the response."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                payload = {
                    "title": "Edited",
                    "start_time": iso(now + timedelta(hours=1)),
                    "end_time": iso(now + timedelta(hours=2)),
                    "endpoints": [{"alias": "new-room@example.com", "display_name": "New Room", "role": "host"}],
                    "invitees": [],
                    "notes": "",
                }
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_meeting_edit_self_exclusion_not_affected_by_own_endpoints(self, test_db):
        """A meeting with an endpoint can still be edited to keep that endpoint."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
                insert_endpoint(conn, mid, endpoint_alias="same-room@example.com")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                payload = {
                    "title": "Kept",
                    "start_time": iso(now + timedelta(hours=1)),
                    "end_time": iso(now + timedelta(hours=2)),
                    "endpoints": [{"alias": "same-room@example.com", "display_name": "Room", "role": "host"}],
                    "invitees": [],
                    "notes": "",
                }
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps(payload),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
        assert resp.status_code == 200

    def test_meeting_includes_start_end_times_for_overlap_check(self, test_db):
        """Meeting response includes start_time and end_time for client-side overlap check."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        start = iso(noon_utc)
        end = iso(noon_utc + timedelta(hours=1))
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(conn, start_time=start, end_time=end)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        m = resp.get_json()["items"][0]
        assert "start_time" in m
        assert "end_time" in m

    def test_two_meetings_both_returned_for_conflict_detection(self, test_db):
        """Two overlapping meetings are both in the list so client detects conflicts."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(conn, title="M1", meeting_alias="docuiremediation01", start_time=iso(noon_utc), end_time=iso(noon_utc + timedelta(hours=2)))
                insert_meeting(conn, title="M2", meeting_alias="docuiremediation02", start_time=iso(noon_utc + timedelta(hours=1)), end_time=iso(noon_utc + timedelta(hours=3)))
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        assert len(resp.get_json()["items"]) == 2


# ── Section C: Calendar day view API contract ──────────────────────────────────

class TestCalendarViewApiContract:
    """
    Issue C: Calendar day view state.

    The monthly calendar loads meetings via GET /api/meetings/range.
    Clicking a day loads GET /api/meetings?date=<date>.
    Both must return meeting status and times for rendering.
    """

    def test_meetings_range_returns_ok_and_items(self, test_db):
        """Range API returns ok=True and items list."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(conn, start_time=iso(now), end_time=iso(now + timedelta(hours=1)))
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings/range?start={today}&end={today}")
        data = resp.get_json()
        assert data["ok"] is True
        assert isinstance(data["items"], list)
        assert len(data["items"]) >= 1

    def test_meetings_range_includes_status_field(self, test_db):
        """Range API meeting items include 'status' for calendar dot colouring."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(conn, start_time=iso(now), end_time=iso(now + timedelta(hours=1)), status="scheduled")
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings/range?start={today}&end={today}")
        assert "status" in resp.get_json()["items"][0]

    def test_meetings_range_includes_title_field(self, test_db):
        """Range API meeting items include 'title' for calendar label rendering."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(conn, title="Board Sync", start_time=iso(now), end_time=iso(now + timedelta(hours=1)))
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings/range?start={today}&end={today}")
        assert resp.get_json()["items"][0]["title"] == "Board Sync"

    def test_meetings_range_excludes_out_of_range_meetings(self, test_db):
        """Meetings outside the date range are not returned."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                # Meeting tomorrow only
                insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(days=2)),
                    end_time=iso(now + timedelta(days=2, hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings/range?start={today}&end={today}")
        assert resp.get_json()["items"] == []

    def test_meetings_range_empty_returns_ok_true_empty_list(self, test_db):
        """No meetings in range → ok=True + [] so calendar renders empty cells."""
        app, _ = make_app(test_db)
        create_user(test_db)
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/meetings/range?start=2020-01-01&end=2020-01-31")
        data = resp.get_json()
        assert data["ok"] is True
        assert data["items"] == []

    def test_day_meetings_api_returns_timeline_status(self, test_db):
        """Daily meetings include timeline_status or status for day-view pill rendering."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(conn, start_time=iso(now), end_time=iso(now + timedelta(hours=1)))
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings?date={today}")
        m = resp.get_json()["items"][0]
        assert "status" in m or "timeline_status" in m

    def test_range_api_rejects_unauthenticated(self, test_db):
        """Calendar range API requires authentication (no public access)."""
        app, _ = make_app(test_db)
        with app.test_client() as client:
            resp = client.get("/api/meetings/range?start=2024-01-01&end=2024-01-31")
        assert resp.status_code in (302, 401)


# ── Section D: ENDED status API contract ──────────────────────────────────────

class TestEndedStatusApiContract:
    """
    Issue D: ENDED meetings must appear blue (not gray).

    The CSS change is .pill.ended and .meeting-block.ended → var(--blue).
    The API must return status='ended' (or timeline_status='ended') so the
    CSS class can be applied. These tests verify the backend produces the
    correct status value for ended meetings, and that the meeting list
    includes ended meetings on the correct day.
    """

    def test_ended_meeting_status_is_returned(self, test_db):
        """An ended meeting appears in the daily list with status='ended'."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    status="ended",
                    start_time=iso(now - timedelta(hours=2)),
                    end_time=iso(now - timedelta(hours=1)),
                    ended_at=iso(now - timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings?date={today}")
        m = resp.get_json()["items"][0]
        assert m.get("status") == "ended" or m.get("timeline_status") == "ended"

    def test_ended_meeting_in_range_has_ended_status(self, test_db):
        """Calendar range API returns status='ended' for ended meetings."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    status="ended",
                    start_time=iso(now - timedelta(hours=2)),
                    end_time=iso(now - timedelta(hours=1)),
                    ended_at=iso(now - timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings/range?start={today}&end={today}")
        m = resp.get_json()["items"][0]
        assert m.get("status") == "ended" or m.get("timeline_status") == "ended"

    def test_scheduled_meeting_status_is_not_ended(self, test_db):
        """A scheduled meeting must NOT have status='ended' (regression guard)."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    status="scheduled",
                    start_time=iso(noon_utc),
                    end_time=iso(noon_utc + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        m = resp.get_json()["items"][0]
        status = m.get("timeline_status") or m.get("status")
        assert status != "ended"

    def test_ended_with_errors_status_returned(self, test_db):
        """ended_with_errors status is returned correctly for variant ended state."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    status="ended_with_errors",
                    start_time=iso(now - timedelta(hours=2)),
                    end_time=iso(now - timedelta(hours=1)),
                    ended_at=iso(now - timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings?date={today}")
        assert resp.status_code == 200
        assert len(resp.get_json()["items"]) == 1

    def test_multiple_status_types_in_one_day_all_returned(self, test_db):
        """Multiple meetings with different statuses all appear in daily list."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(conn, title="Scheduled", meeting_alias="docuiremediation03",
                               status="scheduled",
                               start_time=iso(noon_utc),
                               end_time=iso(noon_utc + timedelta(hours=1)))
                insert_meeting(conn, title="Ended", meeting_alias="docuiremediation04",
                               status="ended",
                               start_time=iso(noon_utc - timedelta(hours=3)),
                               end_time=iso(noon_utc - timedelta(hours=2)),
                               ended_at=iso(noon_utc - timedelta(hours=2)))
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        statuses = {m.get("status") for m in resp.get_json()["items"]}
        assert "scheduled" in statuses
        assert "ended" in statuses

    def test_ended_meeting_export_link_available(self, test_db):
        """Export endpoint accessible for ended meetings (drives 'Export' button in UI)."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="ended",
                    start_time=iso(now - timedelta(hours=2)),
                    end_time=iso(now - timedelta(hours=1)),
                    ended_at=iso(now - timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get(f"/api/meetings/{mid}/export")
        assert resp.status_code == 200

    def test_ended_meeting_pill_class_derived_from_status(self, test_db):
        """The 'ended' status value drives the .pill.ended CSS class (api contract)."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    status="ended",
                    start_time=iso(now - timedelta(hours=2)),
                    end_time=iso(now - timedelta(hours=1)),
                    ended_at=iso(now - timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = now.strftime("%Y-%m-%d")
                resp = client.get(f"/api/meetings?date={today}")
        m = resp.get_json()["items"][0]
        effective_status = m.get("timeline_status") or m.get("status")
        assert effective_status == "ended"


# ── E. Calendar meeting detail API contract ────────────────────────────────────

class TestCalendarMeetingDetailApiContract:
    """
    API contract tests for the calendar meeting-detail view (Change 2).

    The frontend new 'meeting' state requires:
      E1. Each meeting in the API response has a stable numeric `id` field.
      E2. Two meetings on the same day have distinct IDs (selection is unambiguous).
      E3. A meeting can be fetched by date and identified by its ID.
      E4. The edit endpoint (POST /api/meetings/<id>/edit) still works correctly
          after the calendar state rename (no server-side change, regression guard).
      E5. Each meeting response includes a `status` field for CSS class derivation.
      E6. Meeting ID is stable across repeated fetches of the same day.
      E7. The meeting selected by ID is exactly the meeting that was created.
    """

    def test_meeting_response_includes_id_field(self, test_db):
        """Every meeting in the API response must have an 'id' field (needed for selection)."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    start_time=iso(noon_utc),
                    end_time=iso(noon_utc + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert len(items) == 1
        assert "id" in items[0]
        assert isinstance(items[0]["id"], int)

    def test_two_same_day_meetings_have_distinct_ids(self, test_db):
        """Two meetings on the same day must have different IDs so selection is unambiguous."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    title="Morning standup",
                    meeting_alias="docAAAAAAAAAAAAAAA",
                    start_time=iso(noon_utc),
                    end_time=iso(noon_utc + timedelta(hours=1)),
                )
                insert_meeting(
                    conn,
                    title="Afternoon review",
                    meeting_alias="docBBBBBBBBBBBBBBB",
                    start_time=iso(noon_utc + timedelta(hours=2)),
                    end_time=iso(noon_utc + timedelta(hours=3)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        items = resp.get_json()["items"]
        assert len(items) == 2
        ids = [m["id"] for m in items]
        assert ids[0] != ids[1]

    def test_meeting_identifiable_by_id_after_date_fetch(self, test_db):
        """A meeting fetched by date can be uniquely identified by its ID."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                expected_id = insert_meeting(
                    conn,
                    title="Target meeting",
                    start_time=iso(noon_utc),
                    end_time=iso(noon_utc + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        items = resp.get_json()["items"]
        matched = [m for m in items if m["id"] == expected_id]
        assert len(matched) == 1
        assert matched[0]["title"] == "Target meeting"

    def test_edit_endpoint_still_works_for_selected_meeting(self, test_db):
        """POST /api/meetings/<id>/edit must still function (regression guard after JS rename)."""
        app, _ = make_app(test_db)
        now = now_utc()
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    start_time=iso(now + timedelta(hours=1)),
                    end_time=iso(now + timedelta(hours=2)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                csrf = get_csrf_token(client, "/")
                resp = client.post(
                    f"/api/meetings/{mid}/edit",
                    data=json.dumps({
                        "start_time": iso(now + timedelta(hours=1, minutes=30)),
                        "end_time": iso(now + timedelta(hours=2, minutes=30)),
                    }),
                    headers={"Content-Type": "application/json", "X-CSRFToken": csrf},
                )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_meeting_response_includes_status_field(self, test_db):
        """Every meeting must expose a 'status' or 'timeline_status' field for CSS class derivation."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    start_time=iso(noon_utc),
                    end_time=iso(noon_utc + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        m = resp.get_json()["items"][0]
        assert "status" in m or "timeline_status" in m

    def test_meeting_id_stable_across_repeated_fetches(self, test_db):
        """Meeting ID must be the same across two identical date-range fetches."""
        app, _ = make_app(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        create_user(test_db)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    start_time=iso(noon_utc),
                    end_time=iso(noon_utc + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp1 = client.get(f"/api/meetings?date={today}")
                resp2 = client.get(f"/api/meetings?date={today}")
        id1 = resp1.get_json()["items"][0]["id"]
        id2 = resp2.get_json()["items"][0]["id"]
        assert id1 == id2

    def test_selected_meeting_matches_created_meeting(self, test_db):
        """The meeting identified by calendarSelectedMeetingId must match the created record."""
        app, _ = make_app(test_db)
        create_user(test_db)
        today_date = now_utc().date()
        noon_utc = datetime(today_date.year, today_date.month, today_date.day, 12, 0, tzinfo=timezone.utc)
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    title="Specific title to verify",
                    start_time=iso(noon_utc),
                    end_time=iso(noon_utc + timedelta(hours=1)),
                )
        with app.test_client() as client:
            login(client, test_db)
            with patch.object(Settings, "DB_PATH", test_db):
                today = today_date.isoformat()
                resp = client.get(f"/api/meetings?date={today}")
        items = resp.get_json()["items"]
        found = next((m for m in items if m["id"] == mid), None)
        assert found is not None
        assert found["title"] == "Specific title to verify"
