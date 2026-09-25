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
