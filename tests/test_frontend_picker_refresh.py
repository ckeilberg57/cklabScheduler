"""
Source-level regression tests for the endpoint-refresh → picker-refresh
call sequence in app/static/app.js.

No JS runtime is needed.  The tests parse the JS source text to verify
structural guarantees that backend API tests cannot cover:

  A. Successful endpoint refresh invokes refreshSchedulingAvailability()
  B. refreshSchedulingAvailability() is placed AFTER await loadEndpoints()
  C. Failed (catch) path does NOT call refreshSchedulingAvailability()
  D. Valid endpoint selection is preserved (renderEndpoints uses saved state)
  E. Removed endpoint selection is cleared (renderEndpoints iterates fresh list)
  F. Date/time availability is recalculated (renderEndpoints always called)
  G. Valid date/time selection is preserved (setDefaultTimes is conditional)
  H. Invalid/stale date/time is reset (setDefaultTimes called when past/missing)
  I. Current time is a fresh new Date() at refresh time, not a page-load constant
"""
import re
import pathlib

_JS = pathlib.Path(__file__).parent.parent / "app" / "static" / "app.js"


# ── Source extraction helpers ─────────────────────────────────────────────────

def _js():
    return _JS.read_text(encoding="utf-8")


def _brace_block(text, start_offset):
    """
    Starting from start_offset, find the first '{', then return the
    substring from start_offset through the balanced closing '}'.
    Returns None if no balanced block is found.
    """
    depth = 0
    i = start_offset
    started = False
    while i < len(text):
        if text[i] == '{':
            depth += 1
            started = True
        elif text[i] == '}' and started:
            depth -= 1
            if depth == 0:
                return text[start_offset:i + 1]
        i += 1
    return None


def _find_function(js, name):
    """Extract the full text of a named top-level function."""
    m = re.search(rf'\bfunction\s+{re.escape(name)}\s*\(', js)
    if m is None:
        return None
    return _brace_block(js, m.start())


def _find_onclick_handler(js, element_id):
    """
    Extract the full onclick async arrow-function body for a given element id.
    Matches:  $('#<id>').onclick = async () => { ... };
    """
    pattern = rf"\$\(['\"]#{re.escape(element_id)}['\"]\)\.onclick\s*=\s*async\s*\(\)\s*=>"
    m = re.search(pattern, js)
    if m is None:
        return None
    return _brace_block(js, m.start())


def _paren_block(text, start_offset):
    """
    Starting from start_offset, find the first '(' then return the substring
    from start_offset through the balanced closing ')'.
    """
    depth = 0
    i = start_offset
    started = False
    while i < len(text):
        if text[i] == '(':
            depth += 1
            started = True
        elif text[i] == ')' and started:
            depth -= 1
            if depth == 0:
                return text[start_offset:i + 1]
        i += 1
    return None


def _find_interval_with(js, required_strings):
    """
    Find a setInterval(...) call whose full text (including the ms argument
    outside the arrow-function braces) contains ALL required_strings.
    Uses parenthesis-matching so the ms value after the closing } is captured.
    """
    for m in re.finditer(r'setInterval\s*\(', js):
        block = _paren_block(js, m.start() + js[m.start():].index('('))
        if block and all(s in block for s in required_strings):
            return block
    return None


def _try_body(block):
    """Return the text inside the first try{…} in block."""
    m = re.search(r'\btry\s*\{', block)
    if m is None:
        return None
    return _brace_block(block, m.start())


def _catch_body(block):
    """Return the text inside the first catch(…){…} in block."""
    m = re.search(r'\bcatch\s*\(', block)
    if m is None:
        return None
    return _brace_block(block, m.start())


# ── A / B / C — Button handler call sequence ──────────────────────────────────

class TestButtonHandlerCallSequence:
    """
    The #refreshEndpoints onclick handler must:
      A. Call refreshSchedulingAvailability() at all (success path).
      B. Call it AFTER await loadEndpoints() (async ordering).
      C. NOT call it from the catch block (failure isolation).
    """

    def test_A_handler_calls_refresh_scheduling_availability(self):
        """A — Button success path invokes refreshSchedulingAvailability()."""
        handler = _find_onclick_handler(_js(), 'refreshEndpoints')
        assert handler is not None, \
            "#refreshEndpoints onclick handler not found in app.js"
        assert 'refreshSchedulingAvailability()' in handler, \
            "Button handler must call refreshSchedulingAvailability()"

    def test_B_load_endpoints_precedes_refresh_in_try_block(self):
        """B — await loadEndpoints() appears before refreshSchedulingAvailability()."""
        handler = _find_onclick_handler(_js(), 'refreshEndpoints')
        assert handler is not None
        try_body = _try_body(handler)
        assert try_body is not None, "onclick handler must have a try block"
        assert 'await loadEndpoints()' in try_body, \
            "try block must contain await loadEndpoints()"
        assert 'refreshSchedulingAvailability()' in try_body, \
            "try block must contain refreshSchedulingAvailability()"
        load_pos = try_body.index('await loadEndpoints()')
        refresh_pos = try_body.index('refreshSchedulingAvailability()')
        assert load_pos < refresh_pos, (
            "refreshSchedulingAvailability() must appear AFTER await loadEndpoints() "
            "so picker is recalculated only once endpoint data is applied"
        )

    def test_C_refresh_absent_from_catch_block(self):
        """C — Failure path (catch) must NOT call refreshSchedulingAvailability()."""
        handler = _find_onclick_handler(_js(), 'refreshEndpoints')
        assert handler is not None
        catch_body = _catch_body(handler)
        assert catch_body is not None, "onclick handler must have a catch block"
        assert 'refreshSchedulingAvailability()' not in catch_body, (
            "catch block must NOT call refreshSchedulingAvailability(); "
            "a failed refresh must not disturb existing picker state"
        )

    def test_B_refresh_precedes_toast_confirming_sequence(self):
        """B (corollary) — picker refresh happens before the success toast."""
        handler = _find_onclick_handler(_js(), 'refreshEndpoints')
        try_body = _try_body(handler)
        assert try_body is not None
        refresh_pos = try_body.index('refreshSchedulingAvailability()')
        toast_pos = try_body.find('showToast')
        assert 0 <= refresh_pos < toast_pos, \
            "refreshSchedulingAvailability() must precede the success toast"


# ── A / B / C — Periodic auto-refresh call sequence ──────────────────────────

class TestPeriodicIntervalCallSequence:
    """
    The 5-minute endpoint auto-refresh setInterval must mirror the button:
      A. Calls refreshSchedulingAvailability() on success.
      B. After await loadEndpoints().
      C. Not in catch.
    """

    def _interval(self):
        return _find_interval_with(_js(), ['loadEndpoints', '5 * 60 * 1000'])

    def test_A_interval_calls_refresh_scheduling_availability(self):
        """A — 5-minute interval calls refreshSchedulingAvailability()."""
        interval = self._interval()
        assert interval is not None, \
            "5-minute endpoint setInterval block not found in app.js"
        assert 'refreshSchedulingAvailability()' in interval, \
            "5-minute interval must call refreshSchedulingAvailability()"

    def test_B_load_precedes_refresh_in_interval_try(self):
        """B — await loadEndpoints() before refreshSchedulingAvailability()."""
        interval = self._interval()
        assert interval is not None
        try_body = _try_body(interval)
        assert try_body is not None
        load_pos = try_body.index('await loadEndpoints()')
        refresh_pos = try_body.index('refreshSchedulingAvailability()')
        assert load_pos < refresh_pos, \
            "In the interval, refreshSchedulingAvailability() must follow await loadEndpoints()"

    def test_C_refresh_absent_from_interval_catch(self):
        """C — Interval catch block must not call refreshSchedulingAvailability()."""
        interval = self._interval()
        assert interval is not None
        catch_body = _catch_body(interval)
        assert catch_body is not None
        assert 'refreshSchedulingAvailability()' not in catch_body, \
            "Interval catch must not call refreshSchedulingAvailability()"


# ── F / G / H / I — refreshSchedulingAvailability() body ─────────────────────

class TestRefreshSchedulingAvailabilityBody:
    """
    The refreshSchedulingAvailability() function itself must:
      F. Always call renderEndpoints() (availability recalculated unconditionally).
      G. Call setDefaultTimes() only conditionally (preserve valid future times).
      H. Treat past, missing, or NaN start values as stale.
      I. Compute a fresh new Date() at call time, not reuse a page-load constant.
    """

    def _fn(self):
        fn = _find_function(_js(), 'refreshSchedulingAvailability')
        assert fn is not None, "refreshSchedulingAvailability not found in app.js"
        return fn

    def test_F_calls_render_endpoints_unconditionally(self):
        """F — renderEndpoints() is called regardless of whether times were reset."""
        fn = self._fn()
        assert 'renderEndpoints()' in fn, \
            "refreshSchedulingAvailability must call renderEndpoints()"
        # Unconditional: renderEndpoints() must NOT be inside the stale-time if block.
        # Find the if block; renderEndpoints must come after it at the same nesting level.
        if_match = re.search(r'\bif\s*\(', fn)
        assert if_match is not None
        if_block = _brace_block(fn, if_match.start())
        assert if_block is not None
        assert 'renderEndpoints()' not in if_block, \
            "renderEndpoints() must not be inside the conditional — it must always run"
        # Verify it appears after the if block in the function body
        if_end = fn.index(if_block) + len(if_block)
        tail = fn[if_end:]
        assert 'renderEndpoints()' in tail, \
            "renderEndpoints() must appear after the conditional block, not before it"

    def test_G_set_default_times_is_conditional(self):
        """G — setDefaultTimes() is guarded so valid future times are not overwritten."""
        fn = self._fn()
        assert 'setDefaultTimes()' in fn
        # Must be inside an if block
        if_match = re.search(r'\bif\s*\(', fn)
        assert if_match is not None, "function must contain a conditional"
        if_block = _brace_block(fn, if_match.start())
        assert if_block is not None
        assert 'setDefaultTimes()' in if_block, \
            "setDefaultTimes() must be inside the conditional (not called unconditionally)"

    def test_H_condition_guards_past_start_time(self):
        """H — condition resets times when start is in the past."""
        fn = self._fn()
        # Must compare currentStart to now
        assert 'currentStart' in fn and 'now' in fn, \
            "function must compare currentStart to now"
        # Past check: currentStart <= now  (or < now)
        assert re.search(r'currentStart\s*<=?\s*now', fn), \
            "condition must check currentStart <= now (or < now) to detect past times"

    def test_H_condition_guards_missing_start_value(self):
        """H — condition resets times when #startTime is empty."""
        fn = self._fn()
        assert '!currentStart' in fn or re.search(r'currentStart\s*==\s*null', fn), \
            "condition must guard against null/missing currentStart"

    def test_H_condition_guards_nan_date(self):
        """H — condition resets times when #startTime holds an unparseable value."""
        fn = self._fn()
        assert 'isNaN' in fn, \
            "condition must call isNaN() to handle unparseable datetime-local values"

    def test_I_fresh_date_created_inside_function(self):
        """I — new Date() is called inside the function, not from a module-level constant."""
        fn = self._fn()
        assert 'new Date()' in fn, \
            "refreshSchedulingAvailability must call new Date() to get the current time"

    def test_I_date_captured_before_stale_check(self):
        """I — fresh now is captured before the stale-time comparison."""
        fn = self._fn()
        new_date_pos = fn.index('new Date()')
        cond_pos = fn.index('currentStart')
        assert new_date_pos < cond_pos, \
            "new Date() must be captured before the stale-time conditional"

    def test_no_duplicate_load_endpoints_in_refresh_fn(self):
        """No duplicate async fetch inside the picker-refresh function."""
        fn = self._fn()
        assert 'loadEndpoints' not in fn, \
            "refreshSchedulingAvailability must not call loadEndpoints() (no duplication)"

    def test_set_default_times_called_exactly_once(self):
        """G/H — only one call site for setDefaultTimes() in the function."""
        fn = self._fn()
        assert fn.count('setDefaultTimes()') == 1, \
            "refreshSchedulingAvailability must call setDefaultTimes() exactly once"

    def test_render_endpoints_called_exactly_once(self):
        """F — renderEndpoints() called exactly once in the function."""
        fn = self._fn()
        assert fn.count('renderEndpoints()') == 1, \
            "refreshSchedulingAvailability must call renderEndpoints() exactly once"


# ── D / E — renderEndpoints selection preservation ───────────────────────────

class TestEndpointSelectionPreservation:
    """
    D. Valid endpoint selection is preserved: renderEndpoints() restores
       checkboxes for aliases still in the fresh endpoint list.
    E. Removed endpoint selection is cleared: renderEndpoints() only
       iterates state.endpoints; absent aliases produce no checkbox.
    """

    def _render_fn(self):
        fn = _find_function(_js(), 'renderEndpoints')
        assert fn is not None, "renderEndpoints not found in app.js"
        return fn

    def test_D_remember_selections_called_first(self):
        """D — rememberEndpointSelections() saves current checkbox state before rebuild."""
        fn = self._render_fn()
        assert 'rememberEndpointSelections()' in fn, \
            "renderEndpoints must call rememberEndpointSelections() to save selections"

    def test_D_checked_state_restored_from_selected_aliases(self):
        """D — check.checked is set from state.selectedEndpointAliases."""
        fn = self._render_fn()
        assert 'selectedEndpointAliases' in fn, \
            "renderEndpoints must reference selectedEndpointAliases to restore selections"
        assert 'check.checked' in fn, \
            "renderEndpoints must set check.checked"
        # Specifically must use .has() to test membership
        assert '.has(' in fn, \
            "renderEndpoints must use .has() on selectedEndpointAliases Set"

    def test_E_iterates_only_state_endpoints(self):
        """E — the list is built only from state.endpoints; absent aliases have no checkbox."""
        fn = self._render_fn()
        assert 'state.endpoints' in fn, \
            "renderEndpoints must loop over state.endpoints (the fresh server list)"
        # Must use .forEach or similar iteration on state.endpoints
        assert re.search(r'state\.endpoints\.\w+\(', fn), \
            "renderEndpoints must iterate state.endpoints to build the list"

    def test_D_remember_before_rebuild(self):
        """D — rememberEndpointSelections() precedes list.replaceChildren()."""
        fn = self._render_fn()
        remember_pos = fn.index('rememberEndpointSelections()')
        rebuild_pos = fn.index('replaceChildren()')
        assert remember_pos < rebuild_pos, \
            "rememberEndpointSelections() must be called before list.replaceChildren()"


# ── Module-level guard: no stale Date constant at top of app.js ───────────────

class TestNoStaleDateConstant:
    """
    I (global) — app.js must not declare a module-level 'now' or 'today'
    constant from new Date() that would be frozen at page-load time and
    reused by the picker.
    """

    def test_no_module_level_now_constant(self):
        """I — No module-level `const now = new Date()` before any function."""
        js = _js()
        # Find the first function definition
        first_fn = re.search(r'\bfunction\s+\w+\s*\(', js)
        assert first_fn is not None
        preamble = js[:first_fn.start()]
        # Must not have a bare 'const now = new Date()' in module preamble
        assert not re.search(r'\bconst\s+now\s*=\s*new\s+Date\s*\(\s*\)', preamble), (
            "app.js must not define a module-level 'now' constant from new Date(); "
            "such a constant would be frozen at page-load time"
        )

    def test_refresh_fn_new_date_is_local(self):
        """I — The new Date() in refreshSchedulingAvailability is a local variable."""
        js = _js()
        fn = _find_function(js, 'refreshSchedulingAvailability')
        assert fn is not None
        # Must declare 'const now = new Date()' (local), not reference a global
        assert re.search(r'\bconst\s+now\s*=\s*new\s+Date\s*\(\s*\)', fn), \
            "refreshSchedulingAvailability must declare 'const now = new Date()' locally"
