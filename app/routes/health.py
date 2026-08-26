from contextlib import closing

from flask import Blueprint, jsonify

from app.config import Settings
from app.database import db
from app.meeting_utils import now_utc, parse_iso

health_bp = Blueprint("health", __name__)

VERSION = "2.0.0"
HEARTBEAT_STALE_SECONDS = 30


@health_bp.route("/api/health")
def api_health():
    overall_ok = True
    response = {
        "service": "cklabScheduler",
        "version": VERSION,
    }

    db_ok = False
    meeting_count = 0
    try:
        with closing(db()) as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM meetings").fetchone()
            meeting_count = row["cnt"]
            db_ok = True
    except Exception:
        overall_ok = False
    response["database"] = {"ok": db_ok, "meeting_count": meeting_count}
    if not db_ok:
        overall_ok = False

    pexip_configured = all([
        Settings.REG_STATUS_HOST,
        Settings.COMMAND_HOST,
        Settings.API_USER,
        Settings.API_PASS,
    ])
    response["pexip"] = {"configured": pexip_configured}

    o365_configured = (
        bool(Settings.O365_TENANT_ID)
        and bool(Settings.O365_CLIENT_ID)
        and bool(Settings.O365_CLIENT_SECRET)
        and bool(Settings.O365_FROM_MAILBOX)
    ) if Settings.O365_ENABLED else False
    response["o365"] = {
        "enabled": Settings.O365_ENABLED,
        "configured": o365_configured,
    }

    scheduler_ok = False
    scheduler_info = {}
    try:
        with closing(db()) as conn:
            row = conn.execute(
                "SELECT last_seen, worker_pid FROM scheduler_heartbeat WHERE id = 1"
            ).fetchone()
        if row is None:
            scheduler_info = {"ok": False, "status": "never_started"}
        else:
            age = (now_utc() - parse_iso(row["last_seen"])).total_seconds()
            age_int = int(age)
            if age < HEARTBEAT_STALE_SECONDS:
                scheduler_ok = True
                scheduler_info = {"ok": True, "last_heartbeat_seconds_ago": age_int}
            else:
                scheduler_info = {
                    "ok": False,
                    "status": "stale",
                    "last_heartbeat_seconds_ago": age_int,
                }
    except Exception:
        scheduler_info = {"ok": False, "status": "error"}

    if not scheduler_ok:
        overall_ok = False
    response["scheduler_worker"] = scheduler_info
    response["ok"] = overall_ok

    return jsonify(response), (200 if overall_ok else 500)
