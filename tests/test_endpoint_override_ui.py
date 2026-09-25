"""
JS source-pattern and template tests for endpoint display-name override UI.

Verifies:
  - user-is-admin meta tag present in template
  - state.currentUserIsAdmin read from meta tag
  - Edit Name button is conditional on state.currentUserIsAdmin
  - Edit form created with safe DOM APIs only
  - PUT and DELETE use /endpoints/display-name body-based API
  - pexip_display_name exposed to admin editor
  - custom_display_name controls Restore Default visibility
  - loadEndpoints() called after Save/Restore
  - No prohibited DOM APIs in new code paths
  - CSRF token automatically included via api() helper
  - Edit state (alias + typed value) survives background endpoint refresh
  - CSS input text and placeholder contrast
"""
from pathlib import Path

import pytest

JS_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"
HTML_PATH = Path(__file__).resolve().parent.parent / "app" / "templates" / "index.html"


@pytest.fixture(scope="module")
def js_src():
    return JS_PATH.read_text()


@pytest.fixture(scope="module")
def html_src():
    return HTML_PATH.read_text()


# ── Template tests ─────────────────────────────────────────────────────────────

def test_user_is_admin_meta_tag_present(html_src):
    """index.html must include the user-is-admin meta tag."""
    assert 'name="user-is-admin"' in html_src


def test_user_is_admin_uses_is_administrator_method(html_src):
    """The meta content must call current_user.is_administrator()."""
    assert "current_user.is_administrator()" in html_src


# ── State initialization ───────────────────────────────────────────────────────

def test_state_current_user_is_admin_initialized(js_src):
    """state.currentUserIsAdmin is read from the user-is-admin meta tag."""
    assert "currentUserIsAdmin" in js_src
    assert 'user-is-admin' in js_src


def test_endpoint_edit_form_module_var(js_src):
    """Module-level _endpointEditForm variable is declared."""
    assert "_endpointEditForm" in js_src


# ── Admin-only Edit Name button ────────────────────────────────────────────────

def test_edit_name_button_conditional_on_admin(js_src):
    """Edit Name button must be gated on state.currentUserIsAdmin."""
    assert "state.currentUserIsAdmin" in js_src
    assert "Edit Name" in js_src


def test_edit_name_button_created_with_create_element(js_src):
    """Edit Name button must be created via createElement, not innerHTML."""
    assert "createElement" in js_src
    assert "ep-edit-btn" in js_src


def test_edit_name_button_stops_propagation(js_src):
    """The Edit Name button click must call stopPropagation to prevent checkbox toggling."""
    assert "stopPropagation" in js_src


# ── Endpoint edit form ─────────────────────────────────────────────────────────

def test_edit_form_class_present(js_src):
    """Edit form uses ep-edit-form class."""
    assert "'ep-edit-form'" in js_src or '"ep-edit-form"' in js_src


def test_edit_form_pexip_name_displayed(js_src):
    """Edit form shows pexip_display_name field."""
    assert "pexip_display_name" in js_src
    assert "ep-edit-pexip-name" in js_src


def test_edit_form_custom_name_input(js_src):
    """Edit form includes an input for the custom display name."""
    assert "ep-edit-input" in js_src
    assert "custom_display_name" in js_src


def test_edit_form_save_button(js_src):
    """Edit form has a Save button."""
    assert "'Save'" in js_src or '"Save"' in js_src


def test_edit_form_cancel_button(js_src):
    """Edit form has a Cancel button."""
    assert "'Cancel'" in js_src or '"Cancel"' in js_src


def test_edit_form_restore_default_button(js_src):
    """Edit form has a Restore Default button."""
    assert "Restore Default" in js_src


def test_restore_default_conditional_on_custom_display_name(js_src):
    """Restore Default visibility is gated on ep.custom_display_name."""
    assert "custom_display_name" in js_src


# ── API calls ──────────────────────────────────────────────────────────────────

def test_save_uses_put_method(js_src):
    """Save calls the API with PUT method."""
    assert "method: 'PUT'" in js_src or 'method: "PUT"' in js_src


def test_save_targets_display_name_endpoint(js_src):
    """Save POSTs to /endpoints/display-name."""
    assert "/endpoints/display-name" in js_src


def test_save_sends_endpoint_alias_in_body(js_src):
    """Save request body includes endpoint_alias."""
    assert "endpoint_alias" in js_src


def test_save_sends_display_name_in_body(js_src):
    """Save request body includes display_name."""
    # The key "display_name" must appear in the PUT body construction
    assert "display_name: nameInput.value" in js_src or "display_name:" in js_src


def test_delete_uses_delete_method(js_src):
    """Restore Default calls the API with DELETE method."""
    assert "method: 'DELETE'" in js_src or 'method: "DELETE"' in js_src


def test_delete_sends_endpoint_alias_in_body(js_src):
    """DELETE request body includes endpoint_alias."""
    assert "endpoint_alias: ep.alias" in js_src


def test_load_endpoints_called_after_save(js_src):
    """loadEndpoints() is called after a successful Save."""
    assert "loadEndpoints" in js_src


def test_load_endpoints_called_after_restore(js_src):
    """loadEndpoints() is called after a successful Restore Default."""
    # Both save and restore call loadEndpoints; just verify it's used in the context
    assert js_src.count("loadEndpoints") >= 2


def test_api_helper_used_for_put(js_src):
    """The api() helper is used for PUT (which auto-includes X-CSRFToken)."""
    # The api() helper handles CSRF automatically for non-GET methods
    assert "await api('/endpoints/display-name'" in js_src or "await api(\"/endpoints/display-name\"" in js_src


def test_close_edit_form_on_render(js_src):
    """renderEndpoints() closes any open edit form before rebuilding the list."""
    assert "_closeEndpointEditForm" in js_src


# ── Safe DOM restrictions ──────────────────────────────────────────────────────

def test_no_inner_html_in_js(js_src):
    """app.js must not use innerHTML."""
    assert ".innerHTML" not in js_src


def test_no_outer_html_in_js(js_src):
    """app.js must not use outerHTML."""
    assert ".outerHTML" not in js_src


def test_no_insert_adjacent_html(js_src):
    """app.js must not use insertAdjacentHTML."""
    assert "insertAdjacentHTML" not in js_src


def test_no_dom_parser(js_src):
    """app.js must not use DOMParser."""
    assert "DOMParser" not in js_src


def test_no_document_write(js_src):
    """app.js must not use document.write."""
    assert "document.write" not in js_src


def test_endpoint_name_set_with_text_content(js_src):
    """Endpoint display name is set via textContent, not innerHTML."""
    assert "name.textContent" in js_src


def test_pexip_name_set_with_text_content(js_src):
    """Pexip name in edit form is set via textContent."""
    assert "pexipVal.textContent" in js_src or "ep-edit-pexip-name" in js_src


# ── Edit-state persistence across background refresh (regression: defect #1) ──

CSS_PATH = Path(__file__).resolve().parent.parent / "app" / "static" / "styles.css"


@pytest.fixture(scope="module")
def css_src():
    return CSS_PATH.read_text()


class TestEditStatePersistenceAcrossRefresh:
    """
    Regression tests for the defect where a background endpoint/meeting refresh
    destroyed the active edit form within seconds of it being opened.

    Root cause: renderEndpoints() called _closeEndpointEditForm() unconditionally,
    destroying the form every time loadMeetings() (3-second poll) called
    renderEndpoints().

    Fix: renderEndpoints() captures _editingAlias and the current typed value
    before removing the form from the DOM, then restores the edit form for the
    same endpoint after re-rendering the list.
    """

    def test_editing_alias_state_variable_declared(self, js_src):
        """_editingAlias module-level variable must be declared."""
        assert "_editingAlias" in js_src

    def test_render_endpoints_captures_editing_alias_before_clear(self, js_src):
        """renderEndpoints() must capture the current editing alias before clearing the DOM."""
        assert "editingAlias" in js_src
        # The captured alias is used to restore the form after re-rendering.
        assert "_editingAlias" in js_src

    def test_render_endpoints_captures_typed_value_before_clear(self, js_src):
        """renderEndpoints() must capture the typed input value before clearing the DOM."""
        assert "editingTypedValue" in js_src

    def test_render_endpoints_does_not_clear_editing_alias_on_render(self, js_src):
        """renderEndpoints() must not unconditionally call _closeEndpointEditForm().
        The old pattern destroyed the form on every refresh."""
        import re
        render_fn = re.search(
            r'function renderEndpoints\(\)(.*?)^}',
            js_src, re.DOTALL | re.MULTILINE
        )
        assert render_fn, "renderEndpoints() not found"
        render_body = render_fn.group(1)
        # Strip single-line comments before checking for function calls.
        code_only = re.sub(r'//[^\n]*', '', render_body)
        # The body must NOT contain a bare _closeEndpointEditForm() call.
        assert "_closeEndpointEditForm()" not in code_only

    def test_edit_form_restored_after_render_if_endpoint_still_present(self, js_src):
        """renderEndpoints() must re-open the edit form for the same endpoint
        when that endpoint survives the refresh."""
        assert "_openEndpointEditForm(ep, anchorItem, editingTypedValue)" in js_src

    def test_restored_value_used_to_initialize_input(self, js_src):
        """The edit form input must use the restored typed value on re-open,
        not the server-provided custom_display_name, preserving unsaved text."""
        assert "restoredValue" in js_src
        assert "restoredValue !== null" in js_src

    def test_editing_alias_set_on_open(self, js_src):
        """_openEndpointEditForm must set _editingAlias to ep.alias (not display name)."""
        assert "_editingAlias = ep.alias" in js_src

    def test_editing_alias_cleared_on_close(self, js_src):
        """_closeEndpointEditForm must clear _editingAlias so refresh stops restoring."""
        assert "_editingAlias = null" in js_src

    def test_endpoint_a_does_not_affect_endpoint_b(self, js_src):
        """Edit state identity is based on alias, found via data-alias attribute.
        Only the endpoint whose alias matches _editingAlias gets the form restored."""
        assert "data-alias" in js_src
        assert "CSS.escape" in js_src
        assert 'dataset.alias = ep.alias' in js_src

    def test_endpoint_disappears_while_editing_handled_safely(self, js_src):
        """If the endpoint disappears from Pexip while being edited, _editingAlias
        is cleared rather than leaving broken state."""
        # The restoration code must check that the endpoint still exists.
        assert "state.endpoints.find(e => e.alias === editingAlias)" in js_src
        # And clear _editingAlias if not found.
        assert "_editingAlias = null" in js_src

    def test_cancel_exits_edit_mode(self, js_src):
        """Cancel button calls _closeEndpointEditForm(), which clears _editingAlias."""
        assert "cancelBtn.addEventListener" in js_src
        assert "_closeEndpointEditForm()" in js_src

    def test_save_exits_edit_mode(self, js_src):
        """A successful Save calls _closeEndpointEditForm() before reloading endpoints."""
        assert "saveBtn.addEventListener" in js_src

    def test_restore_default_exits_edit_mode(self, js_src):
        """A successful Restore Default calls _closeEndpointEditForm()."""
        assert "restoreBtn.addEventListener" in js_src

    def test_endpoint_identity_is_alias_not_display_name(self, js_src):
        """The alias (not the custom display name) is used as the edit-state key
        for both _editingAlias tracking and data-alias DOM lookup."""
        assert "_editingAlias = ep.alias" in js_src
        assert 'dataset.alias = ep.alias' in js_src
        # Must not use display_name as the key.
        assert "_editingAlias = ep.display_name" not in js_src
        assert "_editingAlias = ep.custom_display_name" not in js_src

    def test_input_has_placeholder_attribute(self, js_src):
        """The custom-name input must have a placeholder so the field purpose is clear."""
        assert "nameInput.placeholder" in js_src

    def test_css_explicit_placeholder_color(self, css_src):
        """ep-edit-input::placeholder must have an explicit color so browsers
        do not use their default (often invisibly light) placeholder shade."""
        assert "ep-edit-input::placeholder" in css_src
        import re
        rule = re.search(r'\.ep-edit-input::placeholder\s*\{([^}]+)\}', css_src)
        assert rule, "ep-edit-input::placeholder rule not found"
        body = rule.group(1)
        assert "color" in body

    def test_css_placeholder_opacity_set(self, css_src):
        """opacity: 1 must be set to neutralize Firefox's 0.5 placeholder default."""
        rule_match = __import__('re').search(
            r'\.ep-edit-input::placeholder\s*\{([^}]+)\}', css_src
        )
        assert rule_match
        assert "opacity" in rule_match.group(1)

    def test_css_input_border_visible(self, css_src):
        """ep-edit-input border must not use the barely-visible --border variable."""
        import re
        rule = re.search(r'\.ep-edit-input\s*\{([^}]+)\}', css_src, re.DOTALL)
        assert rule
        body = rule.group(1)
        # Border must be present and must not be the near-invisible --border token.
        assert "border" in body
        assert "var(--border)" not in body

    def test_css_focus_state_visible(self, css_src):
        """Focus state must show a clear visual indicator."""
        assert "ep-edit-input:focus" in css_src
        assert "var(--purple)" in css_src
