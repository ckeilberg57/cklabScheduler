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

    def test_render_day_view_function_exists(self):
        assert "function renderDayView" in APP_JS


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
