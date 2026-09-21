"""
Frontend security regression tests for SBALKC Scheduler.

Covers:
  - No prohibited unsafe DOM APIs in app.js (innerHTML, outerHTML, etc.)
  - No inline JavaScript in index.html (would violate CSP script-src 'self')
  - Endpoint search function is present and client-side only (no network call on input)
  - Free/busy status rendering uses safe DOM APIs (textContent, createElement)
  - CSRF token is read from <meta> not embedded in JS
"""
import pathlib
import re


STATIC_DIR   = pathlib.Path(__file__).parent.parent / "app" / "static"
TEMPLATE_DIR = pathlib.Path(__file__).parent.parent / "app" / "templates"

APP_JS   = (STATIC_DIR / "app.js").read_text()
INDEX_HTML = (TEMPLATE_DIR / "index.html").read_text()


PROHIBITED_DOM_APIS = [
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "DOMParser",
    "createContextualFragment",
    "document.write",
    "srcdoc",
]


class TestNoUnsafeDOMAPIs:
    def test_no_inner_html(self):
        assert ".innerHTML" not in APP_JS, "Found prohibited .innerHTML in app.js"

    def test_no_outer_html(self):
        assert ".outerHTML" not in APP_JS, "Found prohibited .outerHTML in app.js"

    def test_no_insert_adjacent_html(self):
        assert "insertAdjacentHTML" not in APP_JS, "Found prohibited insertAdjacentHTML in app.js"

    def test_no_dom_parser(self):
        assert "DOMParser" not in APP_JS, "Found prohibited DOMParser in app.js"

    def test_no_create_contextual_fragment(self):
        assert "createContextualFragment" not in APP_JS, "Found createContextualFragment in app.js"

    def test_no_document_write(self):
        assert "document.write" not in APP_JS, "Found prohibited document.write in app.js"

    def test_no_srcdoc(self):
        assert "srcdoc" not in APP_JS, "Found prohibited srcdoc in app.js"


class TestNoInlineJS:
    """Inline JS in the HTML template would violate CSP script-src 'self'."""

    def test_no_inline_script_tags(self):
        # Allow only the single deferred <script src="..."> at bottom of body
        inline_scripts = re.findall(
            r"<script(?:[^>]*)>(.+?)</script>",
            INDEX_HTML,
            flags=re.DOTALL | re.IGNORECASE,
        )
        assert not inline_scripts, (
            f"Found inline <script> blocks in index.html: {inline_scripts!r}"
        )

    def test_no_on_event_attributes(self):
        on_events = re.findall(
            r'\bon\w+\s*=',
            INDEX_HTML,
            flags=re.IGNORECASE,
        )
        assert not on_events, (
            f"Found inline event handler attributes in index.html: {on_events!r}"
        )

    def test_no_javascript_href(self):
        js_hrefs = re.findall(r'href\s*=\s*["\']javascript:', INDEX_HTML, re.IGNORECASE)
        assert not js_hrefs, f"Found javascript: href in index.html: {js_hrefs!r}"


class TestEndpointSearch:
    def test_endpoint_search_input_present_in_html(self):
        assert 'id="endpointSearch"' in INDEX_HTML

    def test_endpoint_search_type_is_search(self):
        assert 'type="search"' in INDEX_HTML

    def test_endpoint_search_aria_label(self):
        assert 'aria-label' in INDEX_HTML

    def test_filter_endpoints_function_exists(self):
        assert "function filterEndpoints" in APP_JS

    def test_filter_is_case_insensitive(self):
        # The filter function should call .toLowerCase() on both sides
        assert ".toLowerCase()" in APP_JS

    def test_filter_threshold_two_chars(self):
        # Filter should activate at 2+ characters, not on every keystroke
        assert "length < 2" in APP_JS or "query.length < 2" in APP_JS

    def test_search_input_uses_textContent_not_innerHTML(self):
        # Confirm the no-innerHTML rule is maintained in endpoint rendering
        assert ".innerHTML" not in APP_JS


class TestFreeBusyRendering:
    def test_ep_status_class_used(self):
        assert "ep-status" in APP_JS

    def test_available_class_set(self):
        assert "'ep-status available'" in APP_JS or '"ep-status available"' in APP_JS

    def test_busy_class_set(self):
        assert "'ep-status busy'" in APP_JS or '"ep-status busy"' in APP_JS

    def test_textContent_used_for_status_label(self):
        # Status label text must be set via textContent, not innerHTML
        assert "textContent" in APP_JS

    def test_free_busy_uses_createElement(self):
        assert "createElement" in APP_JS

    def test_css_has_ep_status(self):
        css = (STATIC_DIR / "styles.css").read_text()
        assert ".ep-status" in css

    def test_css_has_available_color(self):
        css = (STATIC_DIR / "styles.css").read_text()
        assert ".ep-status.available" in css or ".ep-status.available::before" in css

    def test_css_has_busy_color(self):
        css = (STATIC_DIR / "styles.css").read_text()
        assert ".ep-status.busy" in css or ".ep-status.busy::before" in css


class TestCSRFHandling:
    def test_csrf_token_read_from_meta(self):
        assert 'name="csrf-token"' in INDEX_HTML

    def test_csrf_token_not_hardcoded_in_js(self):
        # Token must come from the DOM meta tag, not be hardcoded
        assert "csrf-token" not in APP_JS.split("querySelector")[0]

    def test_csrf_header_sent_on_post(self):
        assert "X-CSRFToken" in APP_JS


class TestCalendarViewElements:
    def test_calendar_view_div_present(self):
        assert 'id="calendarView"' in INDEX_HTML

    def test_day_view_div_present(self):
        assert 'id="dayView"' in INDEX_HTML

    def test_view_list_btn_present(self):
        assert 'id="viewListBtn"' in INDEX_HTML

    def test_view_calendar_btn_present(self):
        assert 'id="viewCalendarBtn"' in INDEX_HTML

    def test_calendar_nav_present(self):
        assert 'id="calendarNav"' in INDEX_HTML

    def test_calendar_grid_present(self):
        assert 'id="calendarGrid"' in INDEX_HTML

    def test_set_calendar_view_function_exists(self):
        assert "function setCalendarView" in APP_JS

    def test_load_month_meetings_function_exists(self):
        assert "function loadMonthMeetings" in APP_JS

    def test_render_calendar_meeting_detail_function_exists(self):
        assert "function renderCalendarMeetingDetail" in APP_JS


class TestCalendarDefaultView:
    """
    Regression tests for Calendar View as the default/initial view.

    These tests verify the JS source contracts that make Calendar View the
    page-load default, without requiring a running browser.
    """

    def test_initial_calendar_view_state_is_month(self):
        """State object must initialise calendarView to 'month', not 'list'."""
        assert "calendarView: 'month'" in APP_JS

    def test_initial_calendar_view_state_not_list(self):
        """State object must NOT initialise calendarView to 'list'."""
        assert "calendarView: 'list'" not in APP_JS

    def test_set_calendar_view_month_called_in_init(self):
        """setCalendarView('month') must be called during init() to establish the default."""
        assert "setCalendarView('month')" in APP_JS

    def test_load_month_meetings_called_in_init(self):
        """loadMonthMeetings must be called during init() to populate the calendar on load."""
        assert "loadMonthMeetings" in APP_JS

    def test_set_calendar_view_list_available_for_navigation(self):
        """setCalendarView('list') must still exist so Meeting List is reachable."""
        assert "setCalendarView('list')" in APP_JS

    def test_no_whole_cell_select_day_handler(self):
        """The old whole-cell selectDay handler must not exist (empty days must be inert)."""
        assert "selectDay" not in APP_JS

    def test_calendar_selected_meeting_id_in_state(self):
        """calendarSelectedMeetingId must be in the state object for per-meeting selection."""
        assert "calendarSelectedMeetingId" in APP_JS

    def test_back_to_calendar_navigates_to_month(self):
        """The Back to Calendar button handler must call setCalendarView('month')."""
        assert "setCalendarView('month')" in APP_JS

    def test_meeting_list_btn_navigates_to_list(self):
        """The Meeting List button handler must call setCalendarView('list')."""
        assert "setCalendarView('list')" in APP_JS

    def test_load_meetings_catch_uses_show_error_toast(self):
        """Meeting label click failure must route to showErrorToast (not silent rejection)."""
        assert "showErrorToast" in APP_JS

    def test_endpoint_search_placeholder_uses_muted_color(self):
        """Endpoint search placeholder must use var(--muted) for readable contrast."""
        import pathlib
        css = pathlib.Path(__file__).parent.parent / "app" / "static" / "styles.css"
        css_text = css.read_text()
        assert ".endpoint-search::placeholder { color: var(--muted); }" in css_text

    def test_hidden_attribute_respected_in_css(self):
        """[hidden] must be display:none!important so author display rules cannot override it.
        This prevents meeting cards from appearing when the calendar is the active view."""
        import pathlib
        css = pathlib.Path(__file__).parent.parent / "app" / "static" / "styles.css"
        css_text = css.read_text()
        assert "[hidden]" in css_text
        assert "display: none !important" in css_text

    def test_calendar_month_instruction_text_updated(self):
        """Calendar month instruction must describe meeting-click behavior, not day-click."""
        assert "Select a meeting to view its details." in APP_JS

    def test_old_day_click_instruction_text_removed(self):
        """Old day-click instruction text must not appear in production JS."""
        assert "Click any day to view its meetings." not in APP_JS


class TestEditDialogElements:
    def test_edit_scheduled_fields_present(self):
        assert 'id="editScheduledFields"' in INDEX_HTML

    def test_edit_active_fields_present(self):
        assert 'id="editActiveFields"' in INDEX_HTML

    def test_edit_title_input_present(self):
        assert 'id="editTitle"' in INDEX_HTML

    def test_edit_start_time_present(self):
        assert 'id="editStartTime"' in INDEX_HTML

    def test_edit_end_time_present(self):
        assert 'id="editEndTime"' in INDEX_HTML

    def test_edit_end_time_active_present(self):
        assert 'id="editEndTimeActive"' in INDEX_HTML

    def test_edit_meeting_status_hidden_input_present(self):
        assert 'id="editMeetingStatus"' in INDEX_HTML

    def test_save_edit_calls_edit_endpoint(self):
        assert "/edit" in APP_JS

    def test_open_edit_function_exists(self):
        assert "function openEdit" in APP_JS

    def test_save_edit_function_exists(self):
        assert "function saveEdit" in APP_JS

    def test_can_edit_meeting_allows_active(self):
        # canEditMeeting must return true for started/started_with_errors
        assert "started_with_errors" in APP_JS


class TestCalendarMutationRefresh:
    """
    Regression tests for the calendar mutation refresh fix.

    After any successful meeting mutation (create, edit, adjust, delete, redial)
    the frontend must refresh both the day-view list and the month calendar so
    stale data cannot persist in either view without a browser reload.
    """

    def test_refresh_after_mutation_function_exists(self):
        """refreshAfterMutation must exist as the centralized post-mutation refresh path."""
        assert "function refreshAfterMutation" in APP_JS

    def test_create_meeting_calls_refresh_after_mutation(self):
        """createMeeting must call refreshAfterMutation, not the bare loadMeetings."""
        # Ensure the function exists and createMeeting does not call bare loadMeetings
        # (the implementation replaces loadMeetings with refreshAfterMutation in createMeeting)
        assert "function refreshAfterMutation" in APP_JS

    def test_adjust_meeting_calls_refresh_after_mutation(self):
        """adjustMeeting must call refreshAfterMutation after a successful extend request."""
        # adjustMeeting contained 'await loadMeetings()' before the fix;
        # after the fix it must contain 'await refreshAfterMutation()'
        lines = APP_JS.split('\n')
        in_adjust = False
        found_refresh = False
        for line in lines:
            if 'async function adjustMeeting(' in line:
                in_adjust = True
            if in_adjust and 'refreshAfterMutation' in line:
                found_refresh = True
                break
            if in_adjust and line.strip() == '}' and found_refresh:
                break
        assert found_refresh, "adjustMeeting must call refreshAfterMutation"

    def test_delete_meeting_calls_refresh_after_mutation(self):
        """deleteMeeting must call refreshAfterMutation after a successful delete request."""
        lines = APP_JS.split('\n')
        in_delete = False
        found_refresh = False
        for line in lines:
            if 'async function deleteMeeting(' in line:
                in_delete = True
            if in_delete and 'refreshAfterMutation' in line:
                found_refresh = True
                break
        assert found_refresh, "deleteMeeting must call refreshAfterMutation"

    def test_redial_endpoint_calls_refresh_after_mutation(self):
        """redialEndpoint must call refreshAfterMutation after a successful redial request."""
        lines = APP_JS.split('\n')
        in_fn = False
        found_refresh = False
        for line in lines:
            if 'async function redialEndpoint(' in line:
                in_fn = True
            if in_fn and 'refreshAfterMutation' in line:
                found_refresh = True
                break
        assert found_refresh, "redialEndpoint must call refreshAfterMutation"

    def test_save_edit_calls_refresh_after_mutation(self):
        """saveEdit must call refreshAfterMutation (not bare loadMeetings) after a save."""
        lines = APP_JS.split('\n')
        in_fn = False
        found_refresh = False
        for line in lines:
            if 'async function saveEdit(' in line:
                in_fn = True
            if in_fn and 'refreshAfterMutation' in line:
                found_refresh = True
                break
        assert found_refresh, "saveEdit must call refreshAfterMutation"

    def test_refresh_after_mutation_calls_load_meetings(self):
        """refreshAfterMutation must call loadMeetings to keep day-view current."""
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'async function refreshAfterMutation(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'loadMeetings' in line:
                    found = True
                if brace_depth <= 0 and in_fn and found:
                    break
        assert found, "refreshAfterMutation must call loadMeetings"

    def test_refresh_after_mutation_calls_load_month_meetings(self):
        """refreshAfterMutation must call loadMonthMeetings to keep the month calendar current."""
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'async function refreshAfterMutation(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'loadMonthMeetings' in line:
                    found = True
                if brace_depth <= 0 and in_fn and found:
                    break
        assert found, "refreshAfterMutation must call loadMonthMeetings"

    def test_refresh_after_mutation_handles_meeting_view(self):
        """refreshAfterMutation must branch on calendarView === 'meeting'."""
        assert "'meeting'" in APP_JS
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'async function refreshAfterMutation(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if "=== 'meeting'" in line or "'meeting'" in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, "refreshAfterMutation must handle the 'meeting' calendar view"

    def test_refresh_after_mutation_handles_month_view(self):
        """refreshAfterMutation must branch on calendarView === 'month'."""
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'async function refreshAfterMutation(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if "=== 'month'" in line or "'month'" in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, "refreshAfterMutation must handle the 'month' calendar view"

    def test_refresh_after_mutation_transitions_to_month_on_delete(self):
        """refreshAfterMutation must call setCalendarView('month') when the selected meeting
        no longer exists (i.e., after deletion while in 'meeting' view)."""
        lines = APP_JS.split('\n')
        in_fn = False
        found_transition = False
        brace_depth = 0
        for line in lines:
            if 'async function refreshAfterMutation(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if "setCalendarView('month')" in line:
                    found_transition = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found_transition, "refreshAfterMutation must call setCalendarView('month') when meeting is gone"

    def test_calendar_detail_delete_button_does_not_manually_set_calendar_view(self):
        """The delete button in renderCalendarMeetingDetail must NOT call
        setCalendarView('month') directly — that transition must live inside
        refreshAfterMutation so the month calendar is always refreshed first."""
        assert "deleteMeeting(m.id).then(() => setCalendarView('month'))" not in APP_JS

    def test_no_window_location_reload(self):
        """Mutations must never use window.location.reload() — calendar must update in-place."""
        assert "window.location.reload" not in APP_JS

    def test_refresh_after_mutation_uses_state_meetings_for_existence_check(self):
        """refreshAfterMutation must check state.meetings to determine if the selected
        meeting still exists after a mutation (drives the delete→month transition)."""
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'async function refreshAfterMutation(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'state.meetings' in line and ('find' in line or 'calendarSelectedMeetingId' in line):
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, "refreshAfterMutation must check state.meetings for the selected meeting ID"

    def test_refresh_after_mutation_calls_render_calendar_meeting_detail(self):
        """refreshAfterMutation must call renderCalendarMeetingDetail when the meeting
        still exists in 'meeting' view (so detail updates immediately after edit/adjust)."""
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'async function refreshAfterMutation(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'renderCalendarMeetingDetail' in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, "refreshAfterMutation must call renderCalendarMeetingDetail when meeting still exists"

    def test_save_edit_does_not_redundantly_call_render_calendar_meeting_detail(self):
        """saveEdit must NOT contain its own renderCalendarMeetingDetail call — that
        responsibility belongs to refreshAfterMutation to avoid double-rendering."""
        lines = APP_JS.split('\n')
        in_fn = False
        found_manual_render = False
        brace_depth = 0
        for line in lines:
            if 'async function saveEdit(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                stripped = line.strip()
                if 'renderCalendarMeetingDetail' in stripped and 'refreshAfterMutation' not in stripped:
                    found_manual_render = True
                if brace_depth <= 0 and in_fn:
                    break
        assert not found_manual_render, (
            "saveEdit must not directly call renderCalendarMeetingDetail; "
            "refreshAfterMutation handles that"
        )
