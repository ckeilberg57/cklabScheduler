import os
import re
import json
import csv
import io
import secrets
import sqlite3
import threading
import base64
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import urlencode

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__, template_folder="templates", static_folder="static")
lock = threading.Lock()


class Settings:
    DB_PATH = str(BASE_DIR / "scheduler.db")

    REG_STATUS_HOST = os.getenv("REG_STATUS_HOST", "cklab-pexmgr.ck-collab-engtest.com")
    COMMAND_HOST = os.getenv("COMMAND_HOST", "cklab-edges.ck-collab-engtest.com")

    API_USER = os.getenv("MGMT_USER", "")
    API_PASS = os.getenv("MGMT_PASS", "")

    VERIFY_TLS = os.getenv("VERIFY_TLS", "false").lower() == "true"
    REG_VERIFY_TLS = os.getenv("REG_VERIFY_TLS", str(VERIFY_TLS)).lower() == "true"
    COMMAND_VERIFY_TLS = os.getenv("COMMAND_VERIFY_TLS", str(VERIFY_TLS)).lower() == "true"

    HOST_PIN = os.getenv("HOST_PIN", "2024")
    CONTROL_DISPLAY_NAME = os.getenv("CONTROL_DISPLAY_NAME", "Whiteglove Controller")
    DIAL_PROTOCOL = os.getenv("DIAL_PROTOCOL", "auto")

    ABOUT_TO_START_MINUTES = int(os.getenv("ABOUT_TO_START_MINUTES", "1"))
    DEFAULT_EXTEND_MINUTES = int(os.getenv("DEFAULT_EXTEND_MINUTES", "15"))
    POLL_SECONDS = int(os.getenv("POLL_SECONDS", "3"))

    O365_ENABLED = os.getenv("O365_ENABLED", "false").lower() == "true"
    O365_TENANT_ID = os.getenv("O365_TENANT_ID", "")
    O365_CLIENT_ID = os.getenv("O365_CLIENT_ID", "")
    O365_CLIENT_SECRET = os.getenv("O365_CLIENT_SECRET", "")
    O365_FROM_MAILBOX = os.getenv("O365_FROM_MAILBOX", "")
    O365_SAVE_TO_SENT_ITEMS = os.getenv("O365_SAVE_TO_SENT_ITEMS", "true").lower() == "true"
    O365_EMAIL_SUBJECT = os.getenv("O365_EMAIL_SUBJECT", "Your Secure Virtual Consultation")
    WEBRTC_BASE_URL = os.getenv("WEBRTC_BASE_URL", f"https://{COMMAND_HOST}/webapp3/m/")

    O365_INCLUDE_ICS = os.getenv("O365_INCLUDE_ICS", "true").lower() == "true"
    O365_TIMEZONE = os.getenv("O365_TIMEZONE", "America/New_York")
    O365_ORGANIZER_NAME = os.getenv("O365_ORGANIZER_NAME", "Pexip Scheduler")
    O365_LOCATION = os.getenv("O365_LOCATION", "Secure Virtual Session")
    O365_ALLOW_PROPOSE_NEW_TIME = os.getenv("O365_ALLOW_PROPOSE_NEW_TIME", "false").lower() == "true"


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


def db():
    conn = sqlite3.connect(Settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                meeting_alias TEXT NOT NULL UNIQUE,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'scheduled',
                started_at TEXT,
                ended_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS meeting_endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                endpoint_alias TEXT NOT NULL,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'host',
                status TEXT NOT NULL DEFAULT 'scheduled',
                dial_response TEXT,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS meeting_invitees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                email TEXT NOT NULL,
                display_name TEXT,
                role TEXT NOT NULL DEFAULT 'guest',
                join_url TEXT,
                email_status TEXT NOT NULL DEFAULT 'pending',
                email_response TEXT,
                sent_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(meeting_id) REFERENCES meetings(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()


def generate_doc_alias():
    token = secrets.token_urlsafe(12)
    token = re.sub(r"[^a-zA-Z0-9]", "", token)
    token = (token + secrets.token_hex(8))[:16]
    return f"doc{token}"


def validate_or_make_alias(alias):
    if alias:
        alias = alias.strip()
        if not DOC_ALIAS_PATTERN.match(alias):
            raise ValueError("Meeting alias must match doc<16 alphanumeric>, e.g. docA1B2C3D4E5F6G7H8")
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
    # RFC5545 line folding at 75 octets. This simple fold is safe for ASCII-heavy fields.
    if len(line) <= 75:
        return line
    out = []
    while len(line) > 75:
        out.append(line[:75])
        line = " " + line[75:]
    out.append(line)
    return "\r\n".join(out)


def build_ics_invite(meeting, invitee, join_url):
    uid = f"pexip-scheduler-{meeting['id']}-{invitee['id']}@{Settings.O365_FROM_MAILBOX or 'pexip-scheduler'}"
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        f"ATTENDEE;CN={ics_escape(attendee_name)};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{invitee['email']}",
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


def get_graph_access_token():
    if not Settings.O365_ENABLED:
        raise RuntimeError("O365 email sending is disabled. Set O365_ENABLED=true in .env.")

    missing = []
    for name, value in [
        ("O365_TENANT_ID", Settings.O365_TENANT_ID),
        ("O365_CLIENT_ID", Settings.O365_CLIENT_ID),
        ("O365_CLIENT_SECRET", Settings.O365_CLIENT_SECRET),
        ("O365_FROM_MAILBOX", Settings.O365_FROM_MAILBOX),
    ]:
        if not value:
            missing.append(name)
    if missing:
        raise RuntimeError(f"Missing O365 configuration: {', '.join(missing)}")

    token_url = f"https://login.microsoftonline.com/{Settings.O365_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": Settings.O365_CLIENT_ID,
        "client_secret": Settings.O365_CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }

    resp = requests.post(token_url, data=payload, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Graph token response did not include access_token: {data}")
    return token


def send_o365_email(to_email, subject, html_body, ics_content=None, ics_filename="invite.ics"):
    token = get_graph_access_token()
    url = f"https://graph.microsoft.com/v1.0/users/{Settings.O365_FROM_MAILBOX}/sendMail"

    message = {
        "subject": subject,
        "body": {
            "contentType": "HTML",
            "content": html_body,
        },
        "toRecipients": [
            {
                "emailAddress": {
                    "address": to_email,
                }
            }
        ],
    }

    if ics_content:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": ics_filename,
                "contentType": "text/calendar; method=REQUEST; charset=utf-8",
                "contentBytes": base64.b64encode(ics_content.encode("utf-8")).decode("ascii"),
            }
        ]

    payload = {
        "message": message,
        "saveToSentItems": Settings.O365_SAVE_TO_SENT_ITEMS,
    }

    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Graph sendMail failed: {resp.status_code} {resp.text}")
    return {"status_code": resp.status_code, "text": resp.text}


def send_invitee_email(conn, meeting, invitee):
    join_url = invitee["join_url"] or build_webrtc_join_url(
        meeting["meeting_alias"],
        invitee["display_name"] or invitee["email"],
    )

    subject = Settings.O365_EMAIL_SUBJECT
    html_body = build_invite_email_html(
        meeting["title"],
        meeting["meeting_alias"],
        join_url,
        meeting["start_time"],
        meeting["end_time"],
        invitee["display_name"] or "",
    )

    ics_content = None
    if Settings.O365_INCLUDE_ICS:
        ics_content = build_ics_invite(meeting, invitee, join_url)

    response = send_o365_email(
        invitee["email"],
        subject,
        html_body,
        ics_content=ics_content,
        ics_filename=f"{meeting['meeting_alias']}.ics",
    )

    sent_at = iso(now_utc())
    conn.execute(
        """
        UPDATE meeting_invitees
        SET join_url = ?, email_status = ?, email_response = ?, sent_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (join_url, "sent", json.dumps(response), sent_at, sent_at, invitee["id"]),
    )
    return response


def send_invites_for_meeting(conn, meeting_id):
    if not Settings.O365_ENABLED:
        return

    meeting = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not meeting:
        return

    invitees = conn.execute(
        "SELECT * FROM meeting_invitees WHERE meeting_id = ?",
        (meeting_id,),
    ).fetchall()

    for invitee in invitees:
        try:
            send_invitee_email(conn, meeting, invitee)
        except Exception as exc:
            updated_at = iso(now_utc())
            conn.execute(
                """
                UPDATE meeting_invitees
                SET email_status = ?, email_response = ?, updated_at = ?
                WHERE id = ?
                """,
                ("error", json.dumps({"error": str(exc)}), updated_at, invitee["id"]),
            )



class PexipAPI:
    def __init__(self):
        self.status_base = f"https://{Settings.REG_STATUS_HOST}"
        self.command_base = f"https://{Settings.COMMAND_HOST}"
        self.client_base = f"https://{Settings.COMMAND_HOST}"
        self.auth = (Settings.API_USER, Settings.API_PASS)
        self.status_verify = Settings.REG_VERIFY_TLS
        self.command_verify = Settings.COMMAND_VERIFY_TLS
        self.client_verify = Settings.COMMAND_VERIFY_TLS

    def _request(self, method, base, path, verify, **kwargs):
        url = f"{base}{path}"
        resp = requests.request(method, url, auth=self.auth, verify=verify, timeout=20, **kwargs)
        resp.raise_for_status()
        if resp.text:
            try:
                return resp.json()
            except Exception:
                return {"text": resp.text}
        return {}

    def _status_request(self, method, path, **kwargs):
        return self._request(method, self.status_base, path, self.status_verify, **kwargs)

    def _command_request(self, method, path, **kwargs):
        return self._request(method, self.command_base, path, self.command_verify, **kwargs)

    def _client_request(self, method, path, **kwargs):
        url = f"{self.client_base}{path}"
        resp = requests.request(method, url, verify=self.client_verify, timeout=20, **kwargs)
        resp.raise_for_status()
        if resp.text:
            try:
                return resp.json()
            except Exception:
                return {"text": resp.text}
        return {}

    def list_registered_endpoints(self):
        data = self._status_request("GET", "/api/admin/status/v1/registration_alias/?limit=1000")
        items = data.get("objects", data if isinstance(data, list) else [])
        results = []

        for item in items:
            alias = (
                item.get("alias")
                or item.get("name")
                or item.get("registration_alias")
                or item.get("local_alias")
                or ""
            )
            if not alias:
                continue

            display_name = (
                item.get("display_name")
                or item.get("description")
                or item.get("device_name")
                or alias
            )

            is_registered = item.get("is_registered")
            if is_registered is None:
                is_registered = item.get("registered")
            if is_registered is None:
                is_registered = True

            results.append({
                "alias": alias,
                "display_name": display_name,
                "protocol": item.get("protocol", ""),
                "is_registered": is_registered,
                "node": item.get("conference_node") or item.get("node") or "",
            })

        results.sort(key=lambda x: x["display_name"].lower())
        return results

    def request_control_token(self, meeting_alias):
        headers = {"Content-Type": "application/json", "pin": Settings.HOST_PIN}
        payload = {"display_name": Settings.CONTROL_DISPLAY_NAME}
        data = self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/request_token",
            headers=headers,
            json=payload,
        )
        result = data.get("result", {})
        token = result.get("token")
        if not token:
            raise RuntimeError(f"No control token returned for {meeting_alias}: {data}")
        return token

    def start_conference(self, meeting_alias, token):
        headers = {"Content-Type": "application/json", "token": token}
        return self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/start_conference",
            headers=headers,
            json={},
        )

    def dial_endpoint_to_meeting(self, meeting_alias, endpoint_alias, token, role="host"):
        payload = {
            "destination": endpoint_alias,
            "protocol": Settings.DIAL_PROTOCOL,
            "role": role.upper(),
        }
        headers = {"Content-Type": "application/json", "token": token}
        return self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/dial",
            headers=headers,
            json=payload,
        )

    def disconnect_conference(self, meeting_alias, token):
        headers = {"Content-Type": "application/json", "token": token}
        return self._client_request(
            "POST",
            f"/api/client/v2/conferences/{meeting_alias}/disconnect",
            headers=headers,
            json={},
        )

    def release_control_token(self, meeting_alias, token):
        headers = {"Content-Type": "application/json", "token": token}
        try:
            return self._client_request(
                "POST",
                f"/api/client/v2/conferences/{meeting_alias}/release_token",
                headers=headers,
                json={},
            )
        except Exception:
            return {}

    def get_live_participants_via_edges(self, meeting_alias):
        token = None
        try:
            token = self.request_control_token(meeting_alias)
            headers = {"Content-Type": "application/json", "token": token}
            data = self._client_request(
                "GET",
                f"/api/client/v2/conferences/{meeting_alias}/participants",
                headers=headers,
            )
            result = data.get("result", data)
            if isinstance(result, dict):
                result = result.get("participants", [])
            return result if isinstance(result, list) else []
        finally:
            if token:
                self.release_control_token(meeting_alias, token)


pexip = PexipAPI()


def normalize_live_participants(raw_participants):
    items = []
    for p in raw_participants:
        display_name = p.get("display_name") or p.get("name") or p.get("local_display_name") or ""
        remote_alias = p.get("remote_alias") or p.get("uri") or p.get("destination") or p.get("address") or ""
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
    if row["started_at"]:
        return "started"

    start_dt = parse_iso(row["start_time"])
    current = now_utc()
    if start_dt - timedelta(minutes=Settings.ABOUT_TO_START_MINUTES) <= current < start_dt:
        return "about_to_start"
    return "scheduled"


def fetch_meeting_with_endpoints(conn, meeting_id):
    meeting = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    if not meeting:
        return None

    endpoint_rows = conn.execute(
        "SELECT * FROM meeting_endpoints WHERE meeting_id = ? ORDER BY display_name, endpoint_alias",
        (meeting_id,),
    ).fetchall()

    raw_live = []
    try:
        if meeting["started_at"] and not meeting["ended_at"]:
            raw_live = pexip.get_live_participants_via_edges(meeting["meeting_alias"])
    except Exception as exc:
        print(f"[LIVE] failed for {meeting['meeting_alias']}: {exc}")

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


def meetings_for_day(conn, day):
    start_local = datetime.fromisoformat(day)
    day_start = start_local.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    rows = conn.execute(
        """
        SELECT * FROM meetings
        WHERE start_time < ? AND end_time >= ?
        ORDER BY start_time ASC
        """,
        (iso(day_end), iso(day_start)),
    ).fetchall()

    return [fetch_meeting_with_endpoints(conn, row["id"]) for row in rows]


def start_due_meetings():
    with lock:
        current = now_utc()
        with closing(db()) as conn:
            due = conn.execute(
                "SELECT * FROM meetings WHERE started_at IS NULL AND end_time > ? AND start_time <= ?",
                (iso(current), iso(current)),
            ).fetchall()

            for row in due:
                endpoint_rows = conn.execute(
                    "SELECT * FROM meeting_endpoints WHERE meeting_id = ?",
                    (row["id"],),
                ).fetchall()

                all_ok = True
                token = None
                try:
                    token = pexip.request_control_token(row["meeting_alias"])
                    try:
                        pexip.start_conference(row["meeting_alias"], token)
                    except Exception:
                        pass

                    for ep in endpoint_rows:
                        try:
                            resp = pexip.dial_endpoint_to_meeting(
                                row["meeting_alias"],
                                ep["endpoint_alias"],
                                token,
                                ep["role"] or "host",
                            )
                            conn.execute(
                                "UPDATE meeting_endpoints SET status = ?, dial_response = ? WHERE id = ?",
                                ("dialed", json.dumps(resp), ep["id"]),
                            )
                        except Exception as exc:
                            all_ok = False
                            conn.execute(
                                "UPDATE meeting_endpoints SET status = ?, dial_response = ? WHERE id = ?",
                                ("error", json.dumps({"error": str(exc)}), ep["id"]),
                            )
                except Exception as exc:
                    all_ok = False
                    print(f"[START] failed for {row['meeting_alias']}: {exc}")
                finally:
                    if token:
                        pexip.release_control_token(row["meeting_alias"], token)

                conn.execute(
                    "UPDATE meetings SET status = ?, started_at = ?, updated_at = ? WHERE id = ?",
                    (
                        "started" if all_ok else "started_with_errors",
                        iso(current),
                        iso(current),
                        row["id"],
                    ),
                )
            conn.commit()


def end_due_meetings():
    with lock:
        current = now_utc()
        with closing(db()) as conn:
            due = conn.execute(
                "SELECT * FROM meetings WHERE ended_at IS NULL AND end_time <= ?",
                (iso(current),),
            ).fetchall()

            for row in due:
                all_ok = True
                token = None
                try:
                    token = pexip.request_control_token(row["meeting_alias"])
                    pexip.disconnect_conference(row["meeting_alias"], token)
                except Exception as exc:
                    all_ok = False
                    print(f"[END] failed for {row['meeting_alias']}: {exc}")
                finally:
                    if token:
                        pexip.release_control_token(row["meeting_alias"], token)

                conn.execute(
                    "UPDATE meetings SET status = ?, ended_at = ?, updated_at = ? WHERE id = ?",
                    (
                        "ended" if all_ok else "ended_with_errors",
                        iso(current),
                        iso(current),
                        row["id"],
                    ),
                )
                conn.execute(
                    "UPDATE meeting_endpoints SET status = ? WHERE meeting_id = ?",
                    ("ended", row["id"]),
                )
            conn.commit()


def scheduler_tick():
    try:
        start_due_meetings()
    except Exception as exc:
        print(f"start_due_meetings failed: {exc}")

    try:
        end_due_meetings()
    except Exception as exc:
        print(f"end_due_meetings failed: {exc}")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
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
        "webrtc_base_url": Settings.WEBRTC_BASE_URL,
        "o365_include_ics": Settings.O365_INCLUDE_ICS,
        "o365_timezone": Settings.O365_TIMEZONE,
    })


@app.route("/api/endpoints")
def api_endpoints():
    try:
        return jsonify({"ok": True, "items": pexip.list_registered_endpoints()})
    except Exception as exc:
        return jsonify({"ok": False, "items": [], "error": str(exc)}), 500


@app.route("/api/meetings")
def api_meetings():
    day = request.args.get("date") or now_utc().date().isoformat()
    with closing(db()) as conn:
        try:
            return jsonify({"ok": True, "items": meetings_for_day(conn, day)})
        except Exception as exc:
            return jsonify({"ok": False, "items": [], "error": str(exc)}), 500


@app.route("/api/meetings", methods=["POST"])
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
        start_dt = parse_iso(start_time)
        end_dt = parse_iso(end_time)
        if end_dt <= start_dt:
            return jsonify({"ok": False, "error": "end_time must be after start_time"}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with lock:
        with closing(db()) as conn:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO meetings (title, meeting_alias, start_time, end_time, status, created_at, updated_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        alias,
                        iso(start_dt),
                        iso(end_dt),
                        "scheduled",
                        iso(now_utc()),
                        iso(now_utc()),
                        notes,
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
                        INSERT INTO meeting_endpoints (meeting_id, endpoint_alias, display_name, role, status)
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
                    display_name = ((inv.get("display_name") or "") if isinstance(inv, dict) else "").strip()
                    join_url = build_webrtc_join_url(alias, display_name or email)
                    conn.execute(
                        """
                        INSERT INTO meeting_invitees
                        (meeting_id, email, display_name, role, join_url, email_status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (meeting_id, email, display_name, "guest", join_url, "pending", created_at, created_at),
                    )

                if invitees and Settings.O365_ENABLED:
                    send_invites_for_meeting(conn, meeting_id)

                conn.commit()
                item = fetch_meeting_with_endpoints(conn, meeting_id)
                return jsonify({"ok": True, "item": item})
            except sqlite3.IntegrityError:
                return jsonify({"ok": False, "error": "Meeting alias already exists"}), 400


@app.route("/api/meetings/<int:meeting_id>/extend", methods=["POST"])
def api_extend_meeting(meeting_id):
    payload = request.get_json(force=True, silent=True) or {}
    minutes = int(payload.get("minutes") or Settings.DEFAULT_EXTEND_MINUTES)

    with lock:
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
                return jsonify({"ok": False, "error": "Live meeting end time must remain in the future"}), 400

            conn.execute(
                "UPDATE meetings SET end_time = ?, updated_at = ? WHERE id = ?",
                (iso(new_end), iso(current), meeting_id),
            )
            conn.commit()
            action = "extended" if minutes > 0 else "shortened" if minutes < 0 else "unchanged"
            return jsonify({"ok": True, "action": action, "minutes": minutes, "item": fetch_meeting_with_endpoints(conn, meeting_id)})


@app.route("/api/meetings/<int:meeting_id>/delete", methods=["POST"])
def api_delete_meeting(meeting_id):
    with lock:
        with closing(db()) as conn:
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if not row:
                return jsonify({"ok": False, "error": "Meeting not found"}), 404

            conn.execute("DELETE FROM meeting_invitees WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM meeting_endpoints WHERE meeting_id = ?", (meeting_id,))
            conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
            conn.commit()
            return jsonify({"ok": True})


@app.route("/api/meetings/<int:meeting_id>/update", methods=["POST"])
def api_update_meeting(meeting_id):
    payload = request.get_json(force=True, silent=True) or {}
    endpoints = payload.get("endpoints") or []
    invitees = payload.get("invitees")
    notes = (payload.get("notes") or "").strip()

    with lock:
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
                    INSERT INTO meeting_endpoints (meeting_id, endpoint_alias, display_name, role, status)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (meeting_id, endpoint_alias, display_name, role, "scheduled"),
                )

            if invitees is not None:
                conn.execute("DELETE FROM meeting_invitees WHERE meeting_id = ?", (meeting_id,))
                updated_at = iso(now_utc())
                seen_invitees = set()
                for inv in invitees:
                    email = safe_email(inv.get("email") if isinstance(inv, dict) else inv)
                    if not email or email.lower() in seen_invitees:
                        continue
                    seen_invitees.add(email.lower())
                    display_name = ((inv.get("display_name") or "") if isinstance(inv, dict) else "").strip()
                    join_url = build_webrtc_join_url(row["meeting_alias"], display_name or email)
                    conn.execute(
                        """
                        INSERT INTO meeting_invitees
                        (meeting_id, email, display_name, role, join_url, email_status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (meeting_id, email, display_name, "guest", join_url, "pending", updated_at, updated_at),
                    )

            conn.execute(
                "UPDATE meetings SET notes = ?, updated_at = ? WHERE id = ?",
                (notes, iso(now_utc()), meeting_id),
            )

            if invitees is not None and Settings.O365_ENABLED:
                send_invites_for_meeting(conn, meeting_id)

            conn.commit()
            return jsonify({"ok": True, "item": fetch_meeting_with_endpoints(conn, meeting_id)})


@app.route("/api/meetings/<int:meeting_id>/redial_endpoint", methods=["POST"])
def api_redial_endpoint(meeting_id):
    payload = request.get_json(force=True, silent=True) or {}
    endpoint_alias = (payload.get("endpoint_alias") or "").strip()
    if not endpoint_alias:
        return jsonify({"ok": False, "error": "endpoint_alias is required"}), 400

    with lock:
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

            token = None
            try:
                token = pexip.request_control_token(meeting["meeting_alias"])
                resp = pexip.dial_endpoint_to_meeting(
                    meeting["meeting_alias"],
                    endpoint_alias,
                    token,
                    ep["role"] or "host",
                )
                conn.execute(
                    "UPDATE meeting_endpoints SET status = ?, dial_response = ? WHERE id = ?",
                    ("redialed", json.dumps(resp), ep["id"]),
                )
                conn.commit()
                return jsonify({"ok": True, "response": resp})
            except Exception as exc:
                return jsonify({"ok": False, "error": str(exc)}), 500
            finally:
                if token:
                    pexip.release_control_token(meeting["meeting_alias"], token)



@app.route("/api/meetings/<int:meeting_id>/export")
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

        lines = []
        def csv_line(values):
            out = []
            for value in values:
                text = "" if value is None else str(value)
                text = text.replace('"', '""')
                out.append(f'"{text}"')
            return ",".join(out)

        lines.append(csv_line(["Section", "Field", "Value", "Extra 1", "Extra 2", "Extra 3"]))
        for field in ["id", "title", "meeting_alias", "start_time", "end_time", "status", "started_at", "ended_at", "created_at", "updated_at", "notes"]:
            lines.append(csv_line(["Meeting", field, meeting[field], "", "", ""]))

        lines.append(csv_line(["Endpoint", "display_name", "endpoint_alias", "role", "status", "dial_response"]))
        for ep in endpoints:
            lines.append(csv_line(["Endpoint", ep["display_name"] or ep["endpoint_alias"], ep["endpoint_alias"], ep["role"], ep["status"], ep["dial_response"] or ""]))

        lines.append(csv_line(["Invitee", "email", "display_name", "role", "email_status", "sent_at"]))
        for inv in invitees:
            lines.append(csv_line(["Invitee", inv["email"], inv["display_name"] or "", inv["role"], inv["email_status"], inv["sent_at"] or ""]))

        body = "\r\n".join(lines) + "\r\n"
        filename = f"meeting-{meeting['meeting_alias']}.csv"
        return Response(
            body,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

@app.route("/api/meetings/<int:meeting_id>/invitees/<int:invitee_id>/resend", methods=["POST"])
def api_resend_invitee(meeting_id, invitee_id):
    with lock:
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
            except Exception as exc:
                conn.execute(
                    """
                    UPDATE meeting_invitees
                    SET email_status = ?, email_response = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("error", json.dumps({"error": str(exc)}), iso(now_utc()), invitee_id),
                )
                conn.commit()
                return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/export/meetings")
def api_export_meetings():
    start = request.args.get("start")
    end = request.args.get("end")

    if not start or not end:
        return jsonify({"ok": False, "error": "start and end query parameters are required in YYYY-MM-DD format"}), 400

    try:
        start_dt = datetime.fromisoformat(start).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(end).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc) + timedelta(days=1)
    except Exception:
        return jsonify({"ok": False, "error": "Invalid date format. Use YYYY-MM-DD."}), 400

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Meeting ID",
        "Title",
        "Alias",
        "Start Time UTC",
        "End Time UTC",
        "Status",
        "Timeline Status",
        "Started At",
        "Ended At",
        "Assigned Endpoints",
        "Endpoint Statuses",
        "WebRTC Invitees",
        "Invitee Email Statuses",
        "Notes",
        "Created At",
        "Updated At",
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

            invitee_rows = []
            try:
                invitee_rows = conn.execute(
                    "SELECT * FROM meeting_invitees WHERE meeting_id = ? ORDER BY email",
                    (row["id"],),
                ).fetchall()
            except Exception:
                invitee_rows = []

            assigned_endpoints = "; ".join([
                f"{ep['display_name'] or ep['endpoint_alias']} <{ep['endpoint_alias']}>"
                for ep in endpoint_rows
            ])

            endpoint_statuses = "; ".join([
                f"{ep['endpoint_alias']}: {ep['status']}"
                for ep in endpoint_rows
            ])

            invitees = "; ".join([
                f"{inv['display_name'] or inv['email']} <{inv['email']}>"
                for inv in invitee_rows
            ])

            invitee_statuses = "; ".join([
                f"{inv['email']}: {inv['email_status']}"
                for inv in invitee_rows
            ])

            writer.writerow([
                row["id"],
                row["title"],
                row["meeting_alias"],
                row["start_time"],
                row["end_time"],
                row["status"],
                classify_meeting(row),
                row["started_at"],
                row["ended_at"],
                assigned_endpoints,
                endpoint_statuses,
                invitees,
                invitee_statuses,
                row["notes"] or "",
                row["created_at"],
                row["updated_at"],
            ])

    filename = f"scheduler_meetings_{start}_to_{end}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        },
    )


init_db()

scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(scheduler_tick, "interval", seconds=10, id="scheduler_tick", replace_existing=True)
if not scheduler.running:
    scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5080, debug=True)