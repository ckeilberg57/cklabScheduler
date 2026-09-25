import re
from contextlib import closing

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from app.auth.decorators import login_required, role_required
from app.config import Settings
from app.database import db
from app.meeting_utils import load_display_overrides, normalize_alias, now_utc, iso

endpoints_bp = Blueprint("endpoints", __name__)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f\x80-\x9f]")
MAX_DISPLAY_NAME_LENGTH = 200


def _validate_display_name(name):
    """Trim and validate a candidate custom display name. Returns (name, error)."""
    name = (name or "").strip()
    if not name:
        return None, "display_name must not be empty"
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        return None, f"display_name must be at most {MAX_DISPLAY_NAME_LENGTH} characters"
    if _CONTROL_CHAR_RE.search(name):
        return None, "display_name must not contain control characters"
    return name, None


@endpoints_bp.route("/api/endpoints")
@login_required
def api_endpoints():
    try:
        items = current_app.pexip.list_registered_endpoints()
        with closing(db()) as conn:
            overrides = load_display_overrides(conn)
        for item in items:
            alias_key = normalize_alias(item.get("alias", ""))
            item["pexip_display_name"] = item.get("display_name") or item.get("alias") or ""
            custom = overrides.get(alias_key)
            item["custom_display_name"] = custom
            if custom:
                item["display_name"] = custom
        return jsonify({"ok": True, "items": items})
    except Exception:
        current_app.logger.exception("Failed to load registered endpoints")
        return jsonify({"ok": False, "items": [], "error": "Unable to load endpoints"}), 500


@endpoints_bp.route("/api/endpoints/display-name", methods=["PUT"])
@role_required("administrator")
def api_set_endpoint_display_name():
    payload = request.get_json(force=True, silent=True) or {}
    raw_alias = (payload.get("endpoint_alias") or "").strip()
    if not raw_alias:
        return jsonify({"ok": False, "error": "endpoint_alias is required"}), 400

    display_name, err = _validate_display_name(payload.get("display_name"))
    if err:
        return jsonify({"ok": False, "error": err}), 400

    alias_key = normalize_alias(raw_alias)
    updated_by = getattr(current_user, "username", "unknown")
    now = iso(now_utc())

    try:
        with closing(db()) as conn:
            conn.execute(
                """
                INSERT INTO endpoint_display_overrides
                    (alias_key, custom_display_name, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(alias_key) DO UPDATE SET
                    custom_display_name = excluded.custom_display_name,
                    updated_at          = excluded.updated_at,
                    updated_by          = excluded.updated_by
                """,
                (alias_key, display_name, now, updated_by),
            )
            conn.commit()
        return jsonify({"ok": True, "alias_key": alias_key, "custom_display_name": display_name})
    except Exception:
        current_app.logger.exception("Failed to set display name override for %s", raw_alias)
        return jsonify({"ok": False, "error": "Unable to save display name"}), 500


@endpoints_bp.route("/api/endpoints/display-name", methods=["DELETE"])
@role_required("administrator")
def api_delete_endpoint_display_name():
    payload = request.get_json(force=True, silent=True) or {}
    raw_alias = (payload.get("endpoint_alias") or "").strip()
    if not raw_alias:
        return jsonify({"ok": False, "error": "endpoint_alias is required"}), 400

    alias_key = normalize_alias(raw_alias)
    try:
        with closing(db()) as conn:
            conn.execute(
                "DELETE FROM endpoint_display_overrides WHERE alias_key = ?",
                (alias_key,),
            )
            conn.commit()
        return jsonify({"ok": True, "alias_key": alias_key})
    except Exception:
        current_app.logger.exception("Failed to delete display name override for %s", raw_alias)
        return jsonify({"ok": False, "error": "Unable to remove display name"}), 500


@endpoints_bp.route("/api/config")
@login_required
def api_config():
    effective_webrtc = (
        Settings.WEBRTC_BASE_URL or f"https://{Settings.COMMAND_HOST}/webapp3/m/"
    )
    return jsonify({
        "ok": True,
        "pattern": "doc<16>",
        "pattern_regex": r"^doc[a-zA-Z0-9]{16}$",
        "about_to_start_minutes": Settings.ABOUT_TO_START_MINUTES,
        "default_extend_minutes": Settings.DEFAULT_EXTEND_MINUTES,
        "poll_seconds": Settings.POLL_SECONDS,
        "reg_status_host": Settings.REG_STATUS_HOST,
        "command_host": Settings.COMMAND_HOST,
        "host_pin_set": bool(Settings.HOST_PIN),
        "o365_enabled": Settings.O365_ENABLED,
        "o365_from_mailbox": Settings.O365_FROM_MAILBOX,
        "webrtc_base_url": effective_webrtc,
        "o365_include_ics": Settings.O365_INCLUDE_ICS,
        "o365_timezone": Settings.O365_TIMEZONE,
    })
