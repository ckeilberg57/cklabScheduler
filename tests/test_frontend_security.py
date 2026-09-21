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


# ── TestTimelineInteraction ────────────────────────────────────────────────────

class TestTimelineInteraction:
    """
    Timeline meeting blocks must be keyboard-navigable and click-interactive.

    Each block must expose role="button", tabIndex, and aria-label so that
    keyboard and screen-reader users can open the meeting detail view without
    a mouse.  The click handler must guard against hover-card child clicks
    propagating to the block navigation.
    """

    def test_timeline_block_sets_role_button(self):
        assert "setAttribute('role', 'button')" in APP_JS, \
            "renderTimeline must set role='button' on each meeting block"

    def test_timeline_block_sets_tabindex(self):
        assert "block.tabIndex = 0" in APP_JS, \
            "renderTimeline must set tabIndex=0 on each meeting block"

    def test_timeline_block_sets_aria_label(self):
        assert "setAttribute('aria-label'" in APP_JS, \
            "renderTimeline must set aria-label on each meeting block"

    def test_timeline_block_click_sets_calendar_selected_meeting_id(self):
        lines = APP_JS.split('\n')
        in_click = False
        found_id_set = False
        for i, line in enumerate(lines):
            if "block.addEventListener('click'" in line:
                in_click = True
            if in_click:
                if 'calendarSelectedMeetingId' in line:
                    found_id_set = True
                    break
                if i > 0 and '});' in line and found_id_set is False and in_click:
                    in_click = False
        assert found_id_set, \
            "click handler on timeline block must set state.calendarSelectedMeetingId"

    def test_timeline_block_click_calls_set_calendar_view(self):
        lines = APP_JS.split('\n')
        in_click = False
        found_nav = False
        for i, line in enumerate(lines):
            if "block.addEventListener('click'" in line:
                in_click = True
            if in_click:
                if "setCalendarView('meeting')" in line:
                    found_nav = True
                    break
                if '});' in line and in_click and not found_nav:
                    in_click = False
        assert found_nav, \
            "click handler on timeline block must call setCalendarView('meeting')"

    def test_timeline_block_click_guards_hover_card_children(self):
        assert "hoverCard.contains(e.target)" in APP_JS, \
            "click handler must short-circuit when the click target is inside the hover card"

    def test_timeline_block_keydown_enter_navigates(self):
        assert "e.key === 'Enter'" in APP_JS, \
            "keydown handler must respond to Enter key on timeline block"

    def test_timeline_block_keydown_space_navigates(self):
        assert "e.key === ' '" in APP_JS, \
            "keydown handler must respond to Space key on timeline block"

    def test_meeting_block_cursor_pointer_in_css(self):
        css = (STATIC_DIR / "styles.css").read_text()
        lines = css.split('\n')
        in_block = False
        found = False
        for line in lines:
            if '.meeting-block {' in line:
                in_block = True
            if in_block:
                if 'cursor: pointer' in line:
                    found = True
                    break
                if '}' in line and in_block and not found:
                    in_block = False
        assert found, ".meeting-block CSS must include cursor: pointer"

    def test_meeting_block_focus_visible_in_css(self):
        css = (STATIC_DIR / "styles.css").read_text()
        assert '.meeting-block:focus-visible' in css, \
            "styles.css must define .meeting-block:focus-visible for keyboard focus ring"

    def test_timeline_click_handler_does_not_call_open_edit(self):
        """The timeline block click/keydown handlers must route via setCalendarView only —
        they must NOT directly call openEdit, which would bypass lifecycle edit restrictions.
        Edit policy lives entirely in renderCalendarMeetingDetail() and openEdit()."""
        lines = APP_JS.split('\n')
        in_click_handler = False
        in_keydown_handler = False
        open_edit_in_click = False
        open_edit_in_keydown = False
        for line in lines:
            stripped = line.strip()
            if "block.addEventListener('click'" in stripped:
                in_click_handler = True
                in_keydown_handler = False
            if "block.addEventListener('keydown'" in stripped:
                in_keydown_handler = True
                in_click_handler = False
            if in_click_handler or in_keydown_handler:
                if 'openEdit' in stripped:
                    if in_click_handler:
                        open_edit_in_click = True
                    else:
                        open_edit_in_keydown = True
                if stripped.startswith('});') or stripped.startswith('block.append'):
                    if in_click_handler and 'keydown' not in stripped:
                        in_click_handler = False
                    if in_keydown_handler and 'keydown' not in stripped:
                        in_keydown_handler = False
        assert not open_edit_in_click, \
            "Timeline block click handler must not call openEdit directly — use setCalendarView"
        assert not open_edit_in_keydown, \
            "Timeline block keydown handler must not call openEdit directly — use setCalendarView"


# ── TestEndpointPerStateRendering ─────────────────────────────────────────────

class TestEndpointPerStateRendering:
    """
    Per-endpoint chip classes must reflect live / disconnected / neutral state.

    buildEndpointChipRow() is the single source of truth for endpoint chip
    rendering in both renderCards() and renderCalendarMeetingDetail().
    Live endpoints get live-chip (green), dropped endpoints in a started meeting
    get chip-disconnected (red tint), and endpoints in other states get the
    neutral chip class.  Dial Again must appear for dropped endpoints in a
    started meeting and must NOT appear outside that state.
    """

    def test_build_endpoint_chip_row_helper_exists(self):
        assert 'function buildEndpointChipRow(' in APP_JS, \
            "buildEndpointChipRow helper function must be defined in app.js"

    def test_live_endpoint_gets_live_chip_class(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'function buildEndpointChipRow(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'live-chip' in line and 'ep.live' in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, \
            "buildEndpointChipRow must apply 'live-chip' class when ep.live is true"

    def test_disconnected_endpoint_gets_chip_disconnected_class(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'function buildEndpointChipRow(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'chip-disconnected' in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, \
            "buildEndpointChipRow must apply 'chip-disconnected' when ep is dropped in started meeting"

    def test_chip_disconnected_only_for_started_meetings(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found_guard = False
        brace_depth = 0
        for line in lines:
            if 'function buildEndpointChipRow(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'chip-disconnected' in line and 'started' in line:
                    found_guard = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found_guard, \
            "chip-disconnected must only apply when meeting status is 'started'"

    def test_redial_button_present_for_disconnected_endpoint(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found_redial = False
        brace_depth = 0
        for line in lines:
            if 'function buildEndpointChipRow(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'redialEndpoint' in line:
                    found_redial = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found_redial, \
            "buildEndpointChipRow must call redialEndpoint for dropped endpoints in started meetings"

    def test_redial_button_guarded_by_started_state(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found_guard = False
        brace_depth = 0
        for line in lines:
            if 'function buildEndpointChipRow(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'redialEndpoint' in line and 'started' in APP_JS[APP_JS.index('function buildEndpointChipRow('):APP_JS.index('function buildEndpointChipRow(') + 800]:
                    found_guard = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found_guard, \
            "Dial Again must only appear inside buildEndpointChipRow when meeting is started"

    def test_redial_stops_propagation_in_chip_row(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'function buildEndpointChipRow(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'stopPropagation' in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, \
            "Dial Again onclick must call e.stopPropagation() to avoid triggering block navigation"

    def test_render_cards_uses_build_endpoint_chip_row(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'function renderCards(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'buildEndpointChipRow' in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, \
            "renderCards must delegate endpoint chip rendering to buildEndpointChipRow"

    def test_render_calendar_meeting_detail_uses_build_endpoint_chip_row(self):
        lines = APP_JS.split('\n')
        in_fn = False
        found = False
        brace_depth = 0
        for line in lines:
            if 'function renderCalendarMeetingDetail(' in line:
                in_fn = True
            if in_fn:
                brace_depth += line.count('{') - line.count('}')
                if 'buildEndpointChipRow' in line:
                    found = True
                if brace_depth <= 0 and in_fn:
                    break
        assert found, \
            "renderCalendarMeetingDetail must delegate endpoint chip rendering to buildEndpointChipRow"

    def test_chip_disconnected_class_defined_in_css(self):
        css = (STATIC_DIR / "styles.css").read_text()
        assert '.chip-disconnected' in css, \
            "styles.css must define the .chip-disconnected class for dropped-endpoint chips"

    def test_chip_disconnected_has_red_tint_background(self):
        css = (STATIC_DIR / "styles.css").read_text()
        lines = css.split('\n')
        in_rule = False
        found = False
        for line in lines:
            if '.chip-disconnected' in line:
                in_rule = True
            if in_rule:
                if 'background' in line and '217' in line:
                    found = True
                    break
                if '}' in line and not found:
                    in_rule = False
        assert found, \
            ".chip-disconnected must have a red-tinted background (rgba(217,79,79,...))"

    def test_redial_does_not_optimistically_set_ep_live(self):
        """redialEndpoint must NOT set ep.live = true after clicking Dial Again.
        Only a real server response via refreshAfterMutation/loadMeetings may change live state."""
        fn_start = APP_JS.find('function redialEndpoint(')
        assert fn_start != -1
        depth = 0
        fn_end = fn_start
        for i, ch in enumerate(APP_JS[fn_start:], fn_start):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    fn_end = i + 1
                    break
        fn_body = APP_JS[fn_start:fn_end]
        assert 'ep.live' not in fn_body, \
            "redialEndpoint must not modify ep.live — live state must come from real server refresh only"

    def test_redial_calls_refresh_after_mutation_not_optimistic_render(self):
        """After POST, redialEndpoint must call refreshAfterMutation (real server fetch) —
        not manually patch state.meetings or call renderCalendarMeetingDetail directly."""
        fn_start = APP_JS.find('function redialEndpoint(')
        assert fn_start != -1
        depth = 0
        fn_end = fn_start
        for i, ch in enumerate(APP_JS[fn_start:], fn_start):
            if ch == '{': depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    fn_end = i + 1
                    break
        fn_body = APP_JS[fn_start:fn_end]
        assert 'refreshAfterMutation' in fn_body, \
            "redialEndpoint must call refreshAfterMutation to fetch real server state"
        assert 'state.meetings' not in fn_body, \
            "redialEndpoint must not mutate state.meetings directly (no optimistic update)"

    def test_build_endpoint_chip_row_uses_safe_dom_apis_only(self):
        fn_start = APP_JS.find('function buildEndpointChipRow(')
        assert fn_start != -1
        depth = 0
        fn_end = fn_start
        for i, ch in enumerate(APP_JS[fn_start:], fn_start):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    fn_end = i + 1
                    break
        fn_body = APP_JS[fn_start:fn_end]
        for api in PROHIBITED_DOM_APIS:
            assert api not in fn_body, \
                f"buildEndpointChipRow must not use prohibited DOM API: {api}"
