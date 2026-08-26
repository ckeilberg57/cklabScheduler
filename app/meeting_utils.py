import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.config import Settings

logger = logging.getLogger(__name__)

DOC_ALIAS_PATTERN = re.compile(r"^doc[a-zA-Z0-9]{16}$")


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    if isinstance(dt, str):
        return dt
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def normalize_alias(value):
    value = (value or "").strip().lower()
    value = re.sub(r"(?i)^sip:", "", value)
    value = value.split(";")[0].split("?")[0].strip()
    return value


def generate_doc_alias():
    token = secrets.token_urlsafe(12)
    token = re.sub(r"[^a-zA-Z0-9]", "", token)
    token = (token + secrets.token_hex(8))[:16]
    return f"doc{token}"


def validate_or_make_alias(alias):
    if alias:
        alias = alias.strip()
        if not DOC_ALIAS_PATTERN.match(alias):
            raise ValueError(
                "Meeting alias must match doc<16 alphanumeric>, e.g. docA1B2C3D4E5F6G7H8"
            )
        return alias
    return generate_doc_alias()


def safe_email(value):
    value = (value or "").strip()
    if not value:
        return ""
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
        raise ValueError(f"Invalid invitee email address: {value}")
    return value


def build_webrtc_join_url(meeting_alias, display_name_or_email="Guest"):
    base = (Settings.WEBRTC_BASE_URL or "").strip()
    if not base:
        base = f"https://{Settings.COMMAND_HOST}/webapp3/m/"

    params = {
        "conference": meeting_alias,
        "name": display_name_or_email or "Guest",
        "role": "guest",
        "join": "1",
    }

    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode(params)}"


def normalize_live_participants(raw_participants):
    items = []
    for p in raw_participants:
        display_name = (
            p.get("display_name") or p.get("name") or p.get("local_display_name") or ""
        )
        remote_alias = (
            p.get("remote_alias") or p.get("uri") or p.get("destination") or p.get("address") or ""
        )
        participant_id = p.get("uuid") or p.get("participant_uuid") or p.get("id") or ""
        role = (p.get("role") or "").lower()
        remote_ip = (
            p.get("remote_address")
            or p.get("remote_ip")
            or p.get("source_address")
            or p.get("ip_address")
            or ""
        )
        items.append({
            "participant_id": participant_id,
            "display_name": display_name,
            "remote_alias": remote_alias,
            "display_name_key": normalize_alias(display_name),
            "remote_alias_key": normalize_alias(remote_alias),
            "role": role,
            "remote_ip": remote_ip,
        })
    return items


def endpoint_matches_live(endpoint_alias, display_name, live_items):
    alias_key = normalize_alias(endpoint_alias)
    name_key = normalize_alias(display_name)
    for item in live_items:
        candidates = {
            item.get("remote_alias_key", ""),
            item.get("display_name_key", ""),
        }
        if alias_key and alias_key in candidates:
            return True
        if name_key and name_key in candidates:
            return True
    return False


def classify_meeting(row):
    if row["status"] in ("ended", "ended_with_errors"):
        return "ended"
    if row["status"] == "starting":
        return "started"
    if row["started_at"]:
        return "started"
    start_dt = parse_iso(row["start_time"])
    current = now_utc()
    if start_dt - timedelta(minutes=Settings.ABOUT_TO_START_MINUTES) <= current < start_dt:
        return "about_to_start"
    return "scheduled"


def fetch_meeting_with_endpoints(conn, meeting_id, pexip=None):
    meeting = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not meeting:
        return None

    endpoint_rows = conn.execute(
        "SELECT * FROM meeting_endpoints WHERE meeting_id = ? ORDER BY display_name, endpoint_alias",
        (meeting_id,),
    ).fetchall()

    raw_live = []
    try:
        if pexip and meeting["started_at"] and not meeting["ended_at"]:
            raw_live = pexip.get_live_participants_via_edges(meeting["meeting_alias"])
    except Exception as exc:
        logger.warning("Live participant fetch failed for %s: %s", meeting["meeting_alias"], exc)

    live_participants = normalize_live_participants(raw_live)

    endpoints = []
    for ep in endpoint_rows:
        endpoint_alias = ep["endpoint_alias"]
        display_name = ep["display_name"] or endpoint_alias
        is_live = endpoint_matches_live(endpoint_alias, display_name, live_participants)
        endpoints.append({
            "id": ep["id"],
            "endpoint_alias": endpoint_alias,
            "display_name": display_name,
            "role": ep["role"],
            "status": ep["status"],
            "dial_response": json.loads(ep["dial_response"]) if ep["dial_response"] else None,
            "live": is_live,
        })

    invitee_rows = conn.execute(
        "SELECT * FROM meeting_invitees WHERE meeting_id = ? ORDER BY email",
        (meeting_id,),
    ).fetchall()

    invitees = []
    for inv in invitee_rows:
        invitees.append({
            "id": inv["id"],
            "email": inv["email"],
            "display_name": inv["display_name"] or "",
            "role": inv["role"],
            "join_url": inv["join_url"] or "",
            "email_status": inv["email_status"],
            "email_response": json.loads(inv["email_response"]) if inv["email_response"] else None,
            "sent_at": inv["sent_at"],
        })

    return {
        "id": meeting["id"],
        "title": meeting["title"],
        "meeting_alias": meeting["meeting_alias"],
        "start_time": meeting["start_time"],
        "end_time": meeting["end_time"],
        "status": meeting["status"],
        "timeline_status": classify_meeting(meeting),
        "started_at": meeting["started_at"],
        "ended_at": meeting["ended_at"],
        "created_at": meeting["created_at"],
        "updated_at": meeting["updated_at"],
        "notes": meeting["notes"] or "",
        "endpoints": endpoints,
        "live_participants": live_participants,
        "invitees": invitees,
    }


def meetings_for_day(conn, day, pexip=None):
    start_local = datetime.fromisoformat(day)
    day_start = start_local.replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )
    day_end = day_start + timedelta(days=1)

    rows = conn.execute(
        """
        SELECT * FROM meetings
        WHERE start_time < ? AND end_time >= ?
        ORDER BY start_time ASC
        """,
        (iso(day_end), iso(day_start)),
    ).fetchall()

    return [fetch_meeting_with_endpoints(conn, row["id"], pexip) for row in rows]


def eastern_label(value):
    dt = parse_iso(value)
    tz = ZoneInfo(Settings.O365_TIMEZONE or "America/New_York")
    dt_local = dt.astimezone(tz)
    tz_abbr = dt_local.tzname() or "ET"
    return dt_local.strftime(f"%A, %B %-d, %Y at %-I:%M %p {tz_abbr}")


def eastern_time_only_label(value):
    dt = parse_iso(value)
    tz = ZoneInfo(Settings.O365_TIMEZONE or "America/New_York")
    dt_local = dt.astimezone(tz)
    tz_abbr = dt_local.tzname() or "ET"
    return dt_local.strftime(f"%-I:%M %p {tz_abbr}")


def ics_escape(value):
    value = str(value or "")
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def ics_datetime_utc(value):
    dt = parse_iso(value).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def fold_ics_line(line):
    if len(line) <= 75:
        return line
    out = []
    while len(line) > 75:
        out.append(line[:75])
        line = " " + line[75:]
    out.append(line)
    return "\r\n".join(out)


def build_ics_invite(meeting, invitee, join_url):
    uid = (
        f"pexip-scheduler-{meeting['id']}-{invitee['id']}"
        f"@{Settings.O365_FROM_MAILBOX or 'pexip-scheduler'}"
    )
    dtstamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    start_utc = ics_datetime_utc(meeting["start_time"])
    end_utc = ics_datetime_utc(meeting["end_time"])

    attendee_name = invitee["display_name"] or invitee["email"]
    organizer_name = Settings.O365_ORGANIZER_NAME or "Pexip Scheduler"
    organizer_email = Settings.O365_FROM_MAILBOX
    title = meeting["title"] or "Secure Virtual Session"
    location = Settings.O365_LOCATION or "Secure Virtual Session"
    description = (
        f"Join secure virtual session: {join_url}\\n\\n"
        f"Conference alias: {meeting['meeting_alias']}"
    )

    allow_propose = "TRUE" if Settings.O365_ALLOW_PROPOSE_NEW_TIME else "FALSE"

    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Pexip//Whiteglove Scheduler//EN",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{ics_escape(uid)}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{start_utc}",
        f"DTEND:{end_utc}",
        f"SUMMARY:{ics_escape(title)}",
        f"DESCRIPTION:{ics_escape(description)}",
        f"LOCATION:{ics_escape(location)}",
        f"ORGANIZER;CN={ics_escape(organizer_name)}:mailto:{organizer_email}",
        (
            f"ATTENDEE;CN={ics_escape(attendee_name)};ROLE=REQ-PARTICIPANT;"
            f"PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{invitee['email']}"
        ),
        "STATUS:CONFIRMED",
        "SEQUENCE:0",
        "TRANSP:OPAQUE",
        f"X-MICROSOFT-CDO-ALLOWPROPOSE:{allow_propose}",
        "X-MICROSOFT-CDO-BUSYSTATUS:BUSY",
        "BEGIN:VALARM",
        "TRIGGER:-PT15M",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{ics_escape(title)}",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]

    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def build_invite_email_html(title, meeting_alias, join_url, start_time, end_time, display_name=""):
    start_label = eastern_label(start_time) if start_time else ""
    end_label = eastern_time_only_label(end_time) if end_time else ""
    greeting = f"Hello {display_name}," if display_name else "Hello,"

    return f"""
    <html>
      <body style="margin:0;padding:0;background:#f5f7fb;font-family:Arial,Helvetica,sans-serif;color:#0a1236;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f5f7fb;padding:24px 0;">
          <tr>
            <td align="center">
              <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:16px;overflow:hidden;border:1px solid #e7e9f2;">
                <tr>
                  <td style="background:#0a2136;color:#f4e6d3;padding:24px 28px;">
                    <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;font-weight:bold;">Secure Virtual Session</div>
                    <div style="font-size:24px;font-weight:bold;margin-top:6px;">{title}</div>
                  </td>
                </tr>
                <tr>
                  <td style="padding:28px;">
                    <p style="font-size:16px;line-height:1.5;margin:0 0 16px;">{greeting}</p>
                    <p style="font-size:16px;line-height:1.5;margin:0 0 20px;">
                      You have been invited to join a secure virtual session.
                    </p>
                    <p style="font-size:14px;line-height:1.5;margin:0 0 20px;color:#66708a;">
                      <strong>Meeting:</strong> {title}<br>
                      <strong>Time:</strong> {start_label} – {end_label}<br>
                      <strong>Conference:</strong> {meeting_alias}
                    </p>
                    <p style="margin:28px 0;">
                      <a href="{join_url}" style="background:#21b66f;color:#ffffff;text-decoration:none;padding:14px 22px;border-radius:8px;font-weight:bold;display:inline-block;">
                        Join Secure Session
                      </a>
                    </p>
                    <p style="font-size:13px;line-height:1.5;color:#66708a;margin:0;">
                      If the button does not work, copy and paste this link into your browser:<br>
                      <a href="{join_url}" style="color:#4a7bd1;">{join_url}</a>
                    </p>
                  </td>
                </tr>
                <tr>
                  <td style="background:#f4f6fb;padding:16px 28px;font-size:12px;color:#66708a;">
                    This invitation was sent by the scheduling system. Please do not forward this link unless authorized.
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """
