import csv
import io
from contextlib import closing
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request

from app.database import db
from app.meeting_utils import classify_meeting, iso

export_bp = Blueprint("export", __name__)


@export_bp.route("/api/meetings/<int:meeting_id>/export")
def api_export_meeting(meeting_id):
    with closing(db()) as conn:
        meeting = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not meeting:
            return jsonify({"ok": False, "error": "Meeting not found"}), 404

        endpoints = conn.execute(
            "SELECT * FROM meeting_endpoints WHERE meeting_id = ? ORDER BY display_name, endpoint_alias",
            (meeting_id,),
        ).fetchall()
        invitees = conn.execute(
            "SELECT * FROM meeting_invitees WHERE meeting_id = ? ORDER BY email",
            (meeting_id,),
        ).fetchall()

        def csv_line(values):
            out = []
            for value in values:
                text = "" if value is None else str(value)
                text = text.replace('"', '""')
                out.append(f'"{text}"')
            return ",".join(out)

        lines = []
        lines.append(csv_line(["Section", "Field", "Value", "Extra 1", "Extra 2", "Extra 3"]))
        for field in [
            "id", "title", "meeting_alias", "start_time", "end_time",
            "status", "started_at", "ended_at", "created_at", "updated_at", "notes",
        ]:
            lines.append(csv_line(["Meeting", field, meeting[field], "", "", ""]))

        lines.append(
            csv_line(["Endpoint", "display_name", "endpoint_alias", "role", "status", "dial_response"])
        )
        for ep in endpoints:
            lines.append(csv_line([
                "Endpoint",
                ep["display_name"] or ep["endpoint_alias"],
                ep["endpoint_alias"],
                ep["role"],
                ep["status"],
                ep["dial_response"] or "",
            ]))

        lines.append(
            csv_line(["Invitee", "email", "display_name", "role", "email_status", "sent_at"])
        )
        for inv in invitees:
            lines.append(csv_line([
                "Invitee",
                inv["email"],
                inv["display_name"] or "",
                inv["role"],
                inv["email_status"],
                inv["sent_at"] or "",
            ]))

        body = "\r\n".join(lines) + "\r\n"
        filename = f"meeting-{meeting['meeting_alias']}.csv"
        return Response(
            body,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


@export_bp.route("/api/export/meetings")
def api_export_meetings():
    start = request.args.get("start")
    end = request.args.get("end")

    if not start or not end:
        return jsonify({
            "ok": False,
            "error": "start and end query parameters are required in YYYY-MM-DD format",
        }), 400

    try:
        start_dt = datetime.fromisoformat(start).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        )
        end_dt = datetime.fromisoformat(end).replace(
            hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
        ) + timedelta(days=1)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid date format. Use YYYY-MM-DD."}), 400

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Meeting ID", "Title", "Alias", "Start Time UTC", "End Time UTC",
        "Status", "Timeline Status", "Started At", "Ended At",
        "Assigned Endpoints", "Endpoint Statuses",
        "WebRTC Invitees", "Invitee Email Statuses",
        "Notes", "Created At", "Updated At",
    ])

    with closing(db()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM meetings
            WHERE start_time < ? AND end_time >= ?
            ORDER BY start_time ASC
            """,
            (iso(end_dt), iso(start_dt)),
        ).fetchall()

        for row in rows:
            endpoint_rows = conn.execute(
                "SELECT * FROM meeting_endpoints WHERE meeting_id = ? ORDER BY display_name, endpoint_alias",
                (row["id"],),
            ).fetchall()

            try:
                invitee_rows = conn.execute(
                    "SELECT * FROM meeting_invitees WHERE meeting_id = ? ORDER BY email",
                    (row["id"],),
                ).fetchall()
            except Exception:
                invitee_rows = []

            assigned_endpoints = "; ".join(
                f"{ep['display_name'] or ep['endpoint_alias']} <{ep['endpoint_alias']}>"
                for ep in endpoint_rows
            )
            endpoint_statuses = "; ".join(
                f"{ep['endpoint_alias']}: {ep['status']}" for ep in endpoint_rows
            )
            invitees_str = "; ".join(
                f"{inv['display_name'] or inv['email']} <{inv['email']}>"
                for inv in invitee_rows
            )
            invitee_statuses = "; ".join(
                f"{inv['email']}: {inv['email_status']}" for inv in invitee_rows
            )

            writer.writerow([
                row["id"], row["title"], row["meeting_alias"],
                row["start_time"], row["end_time"],
                row["status"], classify_meeting(row),
                row["started_at"], row["ended_at"],
                assigned_endpoints, endpoint_statuses,
                invitees_str, invitee_statuses,
                row["notes"] or "", row["created_at"], row["updated_at"],
            ])

    filename = f"scheduler_meetings_{start}_to_{end}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
