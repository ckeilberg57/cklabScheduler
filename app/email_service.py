import base64
import json

import requests

from app.config import Settings
from app.meeting_utils import (
    build_webrtc_join_url,
    build_ics_invite,
    build_invite_email_html,
    iso,
    now_utc,
)


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

    token_url = (
        f"https://login.microsoftonline.com/{Settings.O365_TENANT_ID}/oauth2/v2.0/token"
    )
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
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
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
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
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
        "SELECT * FROM meeting_invitees WHERE meeting_id = ? AND email_status = 'pending'",
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
