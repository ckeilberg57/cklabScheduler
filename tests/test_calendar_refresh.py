"""
Tests for calendar live status/color refresh (Tests 28-41).
JS source contract tests + regression checks.
"""
import pathlib
import re

APP_JS = (pathlib.Path(__file__).parent.parent / "app" / "static" / "app.js").read_text()
INDEX_HTML = (pathlib.Path(__file__).parent.parent / "app" / "templates" / "index.html").read_text()
STATIC_DIR = pathlib.Path(__file__).parent.parent / "app" / "static"


def _extract_fn(js, name):
    start = js.find(f'function {name}(')
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(js[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
    return None


def _extract_setinterval_bodies(js):
    """Extract the body of all setInterval calls."""
    results = []
    idx = 0
    while True:
        pos = js.find('setInterval(', idx)
        if pos == -1:
            break
        depth = 0
        start = pos
        for i, ch in enumerate(js[pos:], pos):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    results.append(js[start:i + 1])
                    idx = i + 1
                    break
        else:
            break
    return results


class TestCalendarLiveRefresh:
    def test_existing_periodic_refresh_updates_calendar(self):
        """Test 28: The existing polling interval also calls loadMonthMeetings."""
        intervals = _extract_setinterval_bodies(APP_JS)
        found = any("loadMonthMeetings" in body for body in intervals)
        assert found, "At least one setInterval body must call loadMonthMeetings for calendar refresh"

    def test_calendar_refresh_conditional_on_view(self):
        """Test 29: loadMonthMeetings in poll is conditional on calendar view being active."""
        intervals = _extract_setinterval_bodies(APP_JS)
        for body in intervals:
            if "loadMonthMeetings" in body:
                assert "calendarView" in body, \
                    "loadMonthMeetings in poll must be conditional on calendarView"
                break

    def test_scheduled_to_active_transition_reflected(self):
        """Test 30: status field present in monthMeetings (used for color rendering)."""
        assert "timeline_status" in APP_JS
        fn = _extract_fn(APP_JS, 'buildCalCell')
        assert fn is not None
        assert "timeline_status" in fn or "status" in fn

    def test_active_to_ended_transition_reflected(self):
        """Test 31: calendar status styling updates — ended status applied to cal-meeting-label."""
        fn = _extract_fn(APP_JS, 'buildCalCell')
        assert fn is not None
        assert "cal-meeting-label" in fn
        assert "ts" in fn or "timeline_status" in fn

    def test_calendar_status_styling_classes_exist(self):
        """Test 32: CSS has status classes used on calendar cells."""
        css = (STATIC_DIR / "styles.css").read_text()
        assert "started" in css
        assert "ended" in css

    def test_timeline_refresh_remains_intact(self):
        """Test 33: Timeline loadMeetings call is preserved in the polling interval."""
        intervals = _extract_setinterval_bodies(APP_JS)
        all_text = ' '.join(intervals)
        assert "loadMeetings" in all_text, "setInterval must still call loadMeetings for timeline"

    def test_selected_meeting_remains_selected_when_present(self):
        """Test 34: When selected meeting still exists after poll, it remains selected."""
        intervals = _extract_setinterval_bodies(APP_JS)
        meeting_interval = next((b for b in intervals if "loadMonthMeetings" in b), None)
        assert meeting_interval is not None
        assert "calendarSelectedMeetingId" in meeting_interval or "renderCalendarMeetingDetail" in meeting_interval

    def test_missing_selected_meeting_returns_to_month_view(self):
        """Test 35: When selected meeting disappears in poll, returns to month view."""
        intervals = _extract_setinterval_bodies(APP_JS)
        meeting_interval = next((b for b in intervals if "loadMonthMeetings" in b), None)
        assert meeting_interval is not None
        assert "setCalendarView('month')" in meeting_interval or 'setCalendarView("month")' in meeting_interval

    def test_no_additional_polling_interval(self):
        """Test 36: No new aggressive polling interval added (only the existing ones)."""
        intervals = _extract_setinterval_bodies(APP_JS)
        short_intervals = []
        for body in intervals:
            matches = re.findall(r',\s*(\d+)\s*\)$', body.strip())
            if matches:
                delay = int(matches[-1])
                if delay < 10000:
                    short_intervals.append(delay)
        # Original had 1 short interval (3000ms) — must not have added another
        assert len(short_intervals) <= 1, \
            f"Must not introduce additional short polling interval; found delays: {short_intervals}"


class TestSecurityAndRegression:
    def test_safe_dom_preserved(self):
        """Test 37: Safe DOM APIs preserved — no prohibited APIs in app.js."""
        prohibited = [
            (".innerHTML", ".innerHTML"),
            (".outerHTML", ".outerHTML"),
            ("insertAdjacentHTML", "insertAdjacentHTML"),
            ("DOMParser", "DOMParser"),
            ("createContextualFragment", "createContextualFragment"),
            ("document.write", "document.write"),
            ("srcdoc", "srcdoc"),
        ]
        for check, label in prohibited:
            assert check not in APP_JS, f"Prohibited DOM API found: {label}"

    def test_csrf_preserved(self):
        """Test 38: CSRF token header still sent on POST."""
        assert "X-CSRFToken" in APP_JS

    def test_timeline_hover_card_preserved(self):
        """Test 39: buildTimelineHoverCard function still exists."""
        assert "function buildTimelineHoverCard(" in APP_JS

    def test_dial_again_preserved(self):
        """Test 40: Dial Again (redialEndpoint) functionality preserved."""
        assert "function redialEndpoint(" in APP_JS
        assert "redial_endpoint" in APP_JS

    def test_scheduled_edit_behavior_preserved(self):
        """Test 41: Scheduled full edit (editScheduledFields) preserved."""
        assert 'id="editScheduledFields"' in INDEX_HTML
        assert "isScheduled" in APP_JS

    def test_active_edit_restrictions_preserved(self):
        """Test 41b: Active edit restricted fields preserved."""
        assert 'id="editActiveFields"' in INDEX_HTML
        assert "isActive" in APP_JS

    def test_independent_endpoint_live_state_preserved(self):
        """Endpoint live state determined by Pexip, not optimistically."""
        fn = _extract_fn(APP_JS, 'redialEndpoint')
        assert fn is not None
        assert 'ep.live' not in fn, "redialEndpoint must not set ep.live optimistically"

    def test_no_disconnect_all_in_remove_endpoint_route(self):
        """Remove endpoint route must not call disconnect_conference (disconnectAll)."""
        meetings_py = (pathlib.Path(__file__).parent.parent / "app" / "routes" / "meetings.py").read_text()
        assert "disconnect_participant" in meetings_py
        start = meetings_py.find("def api_remove_endpoint_from_active")
        assert start != -1
        next_def = meetings_py.find("\ndef ", start + 1)
        fn_body = meetings_py[start:next_def] if next_def != -1 else meetings_py[start:]
        assert "disconnect_conference" not in fn_body, \
            "remove_endpoint must not call disconnect_conference (disconnectAll)"
