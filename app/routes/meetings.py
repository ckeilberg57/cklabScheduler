import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

from app.auth.decorators import login_required
from app.config import Settings
from app.database import db
from app.email_service import send_invites_for_meeting
from app.meeting_utils import (
    build_webrtc_join_url,
    fetch_meeting_with_endpoints,
    iso,
    meetings_for_day,
    meetings_for_range,
    now_utc,
    parse_iso,
    safe_email,
    validate_or_make_alias,
)

meetings_bp = Blueprint("meetings", __name__)


@meetings_bp.route("/api/meetings")
@login_required
def api_meetings():
    day = request.args.get("date") or now_utc().date().isoformat()
    with closing(db()) as conn:
        try:
            return jsonify({
                "ok": True,
                "items": meetings_for_day(conn, day, current_app.pexip),
            })
        except Exception:
            current_app.logger.exception("Failed to load meetings for day %s", day)
            return jsonify({"ok": False, "items": [], "error": "Unable to load meetings"}), 500


@meetings_bp.route("/api/meetings/range")
@login_required
def api_meetings_range():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"ok": False, "error": "start and end dates are required"}), 400
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end)
        if e < s:
            return jsonify({"ok": False, "error": "end must be >= start"}), 400
        if (e - s).days > 366:
            return jsonify({"ok": False, "error": "Date range cannot exceed one year"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid date format. Use YYYY-MM-DD"}), 400
    with closing(db()) as conn:
        try:
            return jsonify({
                "ok": True,
                "items": meetings_for_range(conn, start, end, current_app.pexip),
            })
        except Exception:
            current_app.logger.exception("Failed to load meetings for range %s to %s", start, end)
            return jsonify({"ok": False, "items": [], "error": "Unable to load meetings"}), 500


@meetings_bp.route("/api/meetings", methods=["POST"])
@login_required
def api_create_meeting():
    payload = request.get_json(force=True, silent=True) or {}
    title = (payload.get("title") or "").strip() or "Whiteglove Support Session"
    alias = payload.get("meeting_alias") or ""
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")
    notes = (payload.get("notes") or "").strip()
    endpoints = payload.get("endpoints") or []
    invitees = payload.get("invitees") or []

    if not start_time or not end_time:
        return jsonify({"ok": False, "error": "start_time and end_time are required"}), 400

    try:
        alias = validate_or_make_alias(alias)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    try:
        start_dt = parse_iso(start_time)
        end_dt = parse_iso(end_time)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid date/time format"}), 400

    if end_dt <= start_dt:
        return jsonify({"ok": False, "error": "end_time must be after start_time"}), 400

    with closing(db()) as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO meetings
                    (title, meeting_alias, start_time, end_time, status, created_at, updated_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    title, alias, iso(start_dt), iso(end_dt),
                    "scheduled", iso(now_utc()), iso(now_utc()), notes,
                ),
            )
            meeting_id = cur.lastrowid

            for ep in endpoints:
                endpoint_alias = (ep.get("endpoint_alias") or ep.get("alias") or "").strip()
                if not endpoint_alias:
                    continue
                display_name = (ep.get("display_name") or endpoint_alias).strip()
                role = (ep.get("role") or "host").lower()
                conn.execute(
                    """
                    INSERT INTO meeting_endpoints
                        (meeting_id, endpoint_alias, display_name, role, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (meeting_id, endpoint_alias, display_name, role, "scheduled"),
                )

            created_at = iso(now_utc())
            seen_invitees = set()
            for inv in invitees:
                email = safe_email(inv.get("email") if isinstance(inv, dict) else inv)
                if not email or email.lower() in seen_invitees:
                    continue
                seen_invitees.add(email.lower())
                display_name = (
                    (inv.get("display_name") or "") if isinstance(inv, dict) else ""
                ).strip()
                join_url = build_webrtc_join_url(alias, display_name or email)
                conn.execute(
                    """
                    INSERT INTO meeting_invitees
                        (meeting_id, email, display_name, role, join_url, email_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (meeting_id, email, display_name, "guest", join_url, "pending",
                     created_at, created_at),
                )

            if invitees and Settings.O365_ENABLED:
                send_invites_for_meeting(conn, meeting_id)

            conn.commit()
            item = fetch_meeting_with_endpoints(conn, meeting_id, current_app.pexip)
            return jsonify({"ok": True, "item": item})
        except sqlite3.IntegrityError:
            return jsonify({"ok": False, "error": "Meeting alias already exists"}), 400


@meetings_bp.route("/api/meetings/<int:meeting_id>/extend", methods=["POST"])
@login_required
def api_extend_meeting(meeting_id):
    payload = request.get_json(force=True, silent=True) or {}
    minutes = int(payload.get("minutes") or Settings.DEFAULT_EXTEND_MINUTES)

    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Meeting not found"}), 404

        start_dt = parse_iso(row["start_time"])
        current_end = parse_iso(row["end_time"])
        new_end = current_end + timedelta(minutes=minutes)
        current = now_utc()

        if new_end <= start_dt:
            return jsonify({"ok": False, "error": "End time must remain after the start time"}), 400
        if row["started_at"] and not row["ended_at"] and new_end <= current:
            return jsonify({
                "ok": False,
                "error": "Live meeting end time must remain in the future",
            }), 400

        conn.execute(
            "UPDATE meetings SET end_time = ?, updated_at = ? WHERE id = ?",
            (iso(new_end), iso(current), meeting_id),
        )
        conn.commit()
        action = "extended" if minutes > 0 else "shortened" if minutes < 0 else "unchanged"
        return jsonify({
            "ok": True,
            "action": action,
            "minutes": minutes,
            "item": fetch_meeting_with_endpoints(conn, meeting_id, current_app.pexip),
        })


@meetings_bp.route("/api/meetings/<int:meeting_id>/delete", methods=["POST"])
@login_required
def api_delete_meeting(meeting_id):
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Meeting not found"}), 404

        conn.execute("DELETE FROM meeting_invitees WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM meeting_endpoints WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.commit()
        return jsonify({"ok": True})


@meetings_bp.route("/api/meetings/<int:meeting_id>/update", methods=["POST"])
@login_required
def api_update_meeting(meeting_id):
    payload = request.get_json(force=True, silent=True) or {}
    endpoints = payload.get("endpoints") or []
    invitees = payload.get("invitees")
    notes = (payload.get("notes") or "").strip()

    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Meeting not found"}), 404

        conn.execute("DELETE FROM meeting_endpoints WHERE meeting_id = ?", (meeting_id,))
        for ep in endpoints:
            endpoint_alias = (ep.get("endpoint_alias") or ep.get("alias") or "").strip()
            if not endpoint_alias:
                continue
            display_name = (ep.get("display_name") or endpoint_alias).strip()
            role = (ep.get("role") or "host").lower()
            conn.execute(
                """
                INSERT INTO meeting_endpoints
                    (meeting_id, endpoint_alias, display_name, role, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (meeting_id, endpoint_alias, display_name, role, "scheduled"),
            )

        if invitees is not None:
            existing_sent = {
                r["email"].lower(): r
                for r in conn.execute(
                    "SELECT * FROM meeting_invitees WHERE meeting_id = ? AND email_status = 'sent'",
                    (meeting_id,),
                ).fetchall()
            }

            conn.execute("DELETE FROM meeting_invitees WHERE meeting_id = ?", (meeting_id,))
            updated_at = iso(now_utc())
            seen_invitees = set()
            for inv in invitees:
                email = safe_email(inv.get("email") if isinstance(inv, dict) else inv)
                if not email or email.lower() in seen_invitees:
                    continue
                seen_invitees.add(email.lower())
                display_name = (
                    (inv.get("display_name") or "") if isinstance(inv, dict) else ""
                ).strip()
                key = email.lower()
                if key in existing_sent:
                    old = existing_sent[key]
                    conn.execute(
                        """
                        INSERT INTO meeting_invitees
                            (meeting_id, email, display_name, role, join_url,
                             email_status, email_response, sent_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            meeting_id, email, old["display_name"], old["role"],
                            old["join_url"], "sent", old["email_response"],
                            old["sent_at"], old["created_at"], updated_at,
                        ),
                    )
                else:
                    join_url = build_webrtc_join_url(row["meeting_alias"], display_name or email)
                    conn.execute(
                        """
                        INSERT INTO meeting_invitees
                            (meeting_id, email, display_name, role, join_url,
                             email_status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (meeting_id, email, display_name, "guest", join_url,
                         "pending", updated_at, updated_at),
                    )

        conn.execute(
            "UPDATE meetings SET notes = ?, updated_at = ? WHERE id = ?",
            (notes, iso(now_utc()), meeting_id),
        )

        if invitees is not None and Settings.O365_ENABLED:
            send_invites_for_meeting(conn, meeting_id)

        conn.commit()
        return jsonify({
            "ok": True,
            "item": fetch_meeting_with_endpoints(conn, meeting_id, current_app.pexip),
        })


@meetings_bp.route("/api/meetings/<int:meeting_id>/edit", methods=["POST"])
@login_required
def api_edit_meeting(meeting_id):
    """Comprehensive edit for scheduled or active meetings.

    Scheduled: title, start_time, end_time, endpoints, invitees, notes.
    Active (started/started_with_errors): end_time, notes only.
    All other statuses are rejected.
    """
    payload = request.get_json(force=True, silent=True) or {}

    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "Meeting not found"}), 404

        status = row["status"]
        is_active = status in ("started", "started_with_errors")
        is_scheduled = status == "scheduled"

        if not (is_scheduled or is_active):
            return jsonify({
                "ok": False,
                "error": f"Meetings with status '{status}' cannot be edited",
            }), 400

        current = now_utc()

        if is_scheduled:
            title = (payload.get("title") or "").strip() or row["title"]
            start_time_str = payload.get("start_time")
            end_time_str = payload.get("end_time")
            notes = (payload.get("notes") or "").strip()

            if not start_time_str or not end_time_str:
                return jsonify({"ok": False, "error": "start_time and end_time are required"}), 400

            try:
                start_dt = parse_iso(start_time_str)
                end_dt = parse_iso(end_time_str)
            except Exception:
                return jsonify({"ok": False, "error": "Invalid date/time format"}), 400

            if end_dt <= start_dt:
                return jsonify({"ok": False, "error": "end_time must be after start_time"}), 400

            conn.execute(
                "UPDATE meetings SET title=?, start_time=?, end_time=?, notes=?, updated_at=? WHERE id=?",
                (title, iso(start_dt), iso(end_dt), notes, iso(current), meeting_id),
            )

            endpoints = payload.get("endpoints") or []
            conn.execute("DELETE FROM meeting_endpoints WHERE meeting_id = ?", (meeting_id,))
            for ep in endpoints:
                endpoint_alias = (ep.get("endpoint_alias") or ep.get("alias") or "").strip()
                if not endpoint_alias:
                    continue
                display_name = (ep.get("display_name") or endpoint_alias).strip()
                role = (ep.get("role") or "host").lower()
                conn.execute(
                    """
                    INSERT INTO meeting_endpoints
                        (meeting_id, endpoint_alias, display_name, role, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (meeting_id, endpoint_alias, display_name, role, "scheduled"),
                )

            invitees = payload.get("invitees")
            if invitees is not None:
                existing_sent = {
                    r["email"].lower(): r
                    for r in conn.execute(
                        "SELECT * FROM meeting_invitees WHERE meeting_id = ? AND email_status = 'sent'",
                        (meeting_id,),
                    ).fetchall()
                }
                conn.execute("DELETE FROM meeting_invitees WHERE meeting_id = ?", (meeting_id,))
                updated_at = iso(now_utc())
                seen = set()
                for inv in invitees:
                    email = safe_email(inv.get("email") if isinstance(inv, dict) else inv)
                    if not email or email.lower() in seen:
                        continue
                    seen.add(email.lower())
                    display_name = (
                        (inv.get("display_name") or "") if isinstance(inv, dict) else ""
                    ).strip()
                    key = email.lower()
                    if key in existing_sent:
                        old = existing_sent[key]
                        conn.execute(
                            """
                            INSERT INTO meeting_invitees
                                (meeting_id, email, display_name, role, join_url,
                                 email_status, email_response, sent_at, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                meeting_id, email, old["display_name"], old["role"],
                                old["join_url"], "sent", old["email_response"],
                                old["sent_at"], old["created_at"], updated_at,
                            ),
                        )
                    else:
                        join_url = build_webrtc_join_url(row["meeting_alias"], display_name or email)
                        conn.execute(
                            """
                            INSERT INTO meeting_invitees
                                (meeting_id, email, display_name, role, join_url,
                                 email_status, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (meeting_id, email, display_name, "guest", join_url,
                             "pending", updated_at, updated_at),
                        )
                if Settings.O365_ENABLED:
                    send_invites_for_meeting(conn, meeting_id)

        elif is_active:
            end_time_str = payload.get("end_time")
            notes = (payload.get("notes") or "").strip()

            if not end_time_str:
                return jsonify({"ok": False, "error": "end_time is required"}), 400

            try:
                end_dt = parse_iso(end_time_str)
            except Exception:
                return jsonify({"ok": False, "error": "Invalid date/time format"}), 400

            start_dt = parse_iso(row["start_time"])
            if end_dt <= start_dt:
                return jsonify({"ok": False, "error": "end_time must be after start_time"}), 400

            if end_dt <= current:
                return jsonify({
                    "ok": False,
                    "error": "Active meeting end time must be in the future",
                }), 400

            conn.execute(
                "UPDATE meetings SET end_time=?, notes=?, updated_at=? WHERE id=?",
                (iso(end_dt), notes, iso(current), meeting_id),
            )

        conn.commit()
        return jsonify({
            "ok": True,
            "item": fetch_meeting_with_endpoints(conn, meeting_id, current_app.pexip),
        })


@meetings_bp.route("/api/meetings/<int:meeting_id>/redial_endpoint", methods=["POST"])
@login_required
def api_redial_endpoint(meeting_id):
    payload = request.get_json(force=True, silent=True) or {}
    endpoint_alias = (payload.get("endpoint_alias") or "").strip()
    if not endpoint_alias:
        return jsonify({"ok": False, "error": "endpoint_alias is required"}), 400

    with closing(db()) as conn:
        meeting = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not meeting:
            return jsonify({"ok": False, "error": "Meeting not found"}), 404

        ep = conn.execute(
            "SELECT * FROM meeting_endpoints WHERE meeting_id = ? AND endpoint_alias = ?",
            (meeting_id, endpoint_alias),
        ).fetchone()
        if not ep:
            return jsonify({"ok": False, "error": "Endpoint not assigned to meeting"}), 404

        pexip = current_app.pexip
        token = None
        try:
            token = pexip.request_control_token(meeting["meeting_alias"])
            resp = pexip.dial_endpoint_to_meeting(
                meeting["meeting_alias"], endpoint_alias, token, ep["role"] or "host"
            )
            conn.execute(
                "UPDATE meeting_endpoints SET status = ?, dial_response = ? WHERE id = ?",
                ("redialed", json.dumps(resp), ep["id"]),
            )
            conn.commit()
            return jsonify({"ok": True, "response": resp})
        except Exception:
            current_app.logger.exception(
                "Redial failed for endpoint %s in meeting %d", endpoint_alias, meeting_id
            )
            return jsonify({"ok": False, "error": "Redial failed"}), 500
        finally:
            if token:
                pexip.release_control_token(meeting["meeting_alias"], token)


@meetings_bp.route("/api/meetings/<int:meeting_id>/invitees/<int:invitee_id>/resend", methods=["POST"])
@login_required
def api_resend_invitee(meeting_id, invitee_id):
    from app.email_service import send_invitee_email

    with closing(db()) as conn:
        meeting = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not meeting:
            return jsonify({"ok": False, "error": "Meeting not found"}), 404

        invitee = conn.execute(
            "SELECT * FROM meeting_invitees WHERE id = ? AND meeting_id = ?",
            (invitee_id, meeting_id),
        ).fetchone()
        if not invitee:
            return jsonify({"ok": False, "error": "Invitee not found"}), 404

        try:
            response = send_invitee_email(conn, meeting, invitee)
            conn.commit()
            return jsonify({"ok": True, "response": response})
        except Exception:
            current_app.logger.exception(
                "Failed to resend invite for invitee %d in meeting %d", invitee_id, meeting_id
            )
            conn.execute(
                """
                UPDATE meeting_invitees
                SET email_status = ?, updated_at = ?
                WHERE id = ?
                """,
                ("error", iso(now_utc()), invitee_id),
            )
            conn.commit()
            return jsonify({"ok": False, "error": "Failed to send invitation"}), 500
