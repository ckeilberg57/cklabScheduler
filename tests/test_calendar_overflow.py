"""
Tests for calendar overflow (+N more) expansion feature (Tests 15-27).
JS source contract tests.
"""
import pathlib

APP_JS = (pathlib.Path(__file__).parent.parent / "app" / "static" / "app.js").read_text()
INDEX_HTML = (pathlib.Path(__file__).parent.parent / "app" / "templates" / "index.html").read_text()


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


class TestCalendarOverflow:
    def test_overflow_indicator_appears_when_required(self):
        """Test 15: cal-more is created when dayMeetings.length > maxVisible."""
        assert "cal-more" in APP_JS, "buildCalCell must create .cal-more when overflow"
        assert "dayMeetings.length > maxVisible" in APP_JS or "length > maxVisible" in APP_JS

    def test_overflow_indicator_is_interactive(self):
        """Test 16: The +N more element has role=button and tabindex."""
        assert "role', 'button'" in APP_JS or "'role', \"button\"" in APP_JS
        assert "'tabindex', '0'" in APP_JS or "'tabindex', \"0\"" in APP_JS

    def test_mouse_click_works(self):
        """Test 17: cal-more element has a click event listener."""
        assert "openOverflowPopover" in APP_JS
        assert "addEventListener('click'" in APP_JS

    def test_enter_key_works(self):
        """Test 18: cal-more Enter key opens popover."""
        assert "e.key === 'Enter'" in APP_JS

    def test_space_key_works(self):
        """Test 19: cal-more Space key opens popover."""
        assert "e.key === ' '" in APP_JS

    def test_popover_displays_all_meetings(self):
        """Test 20: openOverflowPopover iterates all dayMeetings."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None, "openOverflowPopover function must exist"
        assert "forEach" in fn or "for" in fn

    def test_meeting_time_displayed(self):
        """Test 21: Popover rows show meeting start time."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None
        assert "cal-overflow-time" in fn
        assert "start_time" in fn or "fmt.format" in fn

    def test_meeting_subject_displayed(self):
        """Test 22: Popover rows show meeting title."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None
        assert "cal-overflow-title" in fn
        assert "title" in fn

    def test_meeting_status_displayed(self):
        """Test 23: Popover rows show meeting status."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None
        assert "cal-overflow-status" in fn
        assert "timeline_status" in fn or "status" in fn

    def test_selecting_overflow_meeting_opens_exact_meeting_detail(self):
        """Test 24: Selecting a meeting from the popover opens the correct meeting detail."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None
        assert "calendarSelectedMeetingId" in fn
        assert "setCalendarView('meeting')" in fn or 'setCalendarView("meeting")' in fn

    def test_stable_meeting_id_used(self):
        """Test 25: The meeting ID (not just title) is used to identify the meeting."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None
        assert "m.id" in fn, "openOverflowPopover must use m.id for meeting selection"

    def test_no_duplicate_meeting_detail_implementation(self):
        """Test 26: The popover uses the existing meeting detail workflow, not a custom one."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None
        assert "buildEndpointChipRow" not in fn, \
            "Overflow popover must not duplicate meeting detail rendering"
        assert "buildInviteeChips" not in fn, \
            "Overflow popover must not duplicate invitee rendering"

    def test_popover_can_close(self):
        """Test 27: closeOverflowPopover function exists and removes the popover."""
        assert "function closeOverflowPopover" in APP_JS
        fn = _extract_fn(APP_JS, 'closeOverflowPopover')
        assert fn is not None
        assert ".remove()" in fn

    def test_popover_safe_dom_only(self):
        """Overflow popover uses only safe DOM APIs."""
        fn = _extract_fn(APP_JS, 'openOverflowPopover')
        assert fn is not None
        for api in ["innerHTML", "outerHTML", "insertAdjacentHTML", "DOMParser",
                    "createContextualFragment", "document.write", "srcdoc"]:
            assert api not in fn, f"openOverflowPopover must not use {api}"
