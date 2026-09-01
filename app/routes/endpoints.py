from flask import Blueprint, current_app, jsonify

from app.auth.decorators import login_required
from app.config import Settings

endpoints_bp = Blueprint("endpoints", __name__)


@endpoints_bp.route("/api/endpoints")
@login_required
def api_endpoints():
    try:
        return jsonify({"ok": True, "items": current_app.pexip.list_registered_endpoints()})
    except Exception as exc:
        return jsonify({"ok": False, "items": [], "error": str(exc)}), 500


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
