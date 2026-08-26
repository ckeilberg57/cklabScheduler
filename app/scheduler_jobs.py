import json
import logging
import os
from contextlib import closing
from datetime import timedelta

from app.config import Settings
from app.database import db
from app.meeting_utils import (
    endpoint_matches_live,
    iso,
    normalize_live_participants,
    now_utc,
    parse_iso,
)
from app.pexip import PexipAPI

logger = logging.getLogger(__name__)

CRASH_RECOVERY_MINUTES = 2

_pexip = PexipAPI()


def _write_heartbeat(conn, worker_pid, worker_start_iso):
    conn.execute(
        "INSERT OR REPLACE INTO scheduler_heartbeat "
        "(id, last_seen, worker_pid, worker_start) VALUES (1, ?, ?, ?)",
        (iso(now_utc()), worker_pid, worker_start_iso),
    )
    conn.commit()


def expire_missed_meetings():
    current = now_utc()
    with closing(db()) as conn:
        candidates = conn.execute(
            "SELECT id, meeting_alias FROM meetings WHERE status = 'scheduled' AND end_time <= ?",
            (iso(current),),
        ).fetchall()
        for row in candidates:
            cur = conn.execute(
                "UPDATE meetings SET status = 'ended_with_errors', ended_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (iso(current), iso(current), row["id"]),
            )
            conn.commit()
            if cur.rowcount == 1:
                logger.warning(
                    "Meeting %s (id=%d): window elapsed before scheduler could start it. "
                    "Marked ended_with_errors. started_at remains NULL.",
                    row["meeting_alias"],
                    row["id"],
                )


def _recover_starting_meeting(conn, row, current):
    meeting_id = row["id"]
    alias = row["meeting_alias"]
    token = None

    try:
        token = _pexip.request_control_token(alias)
    except Exception as exc:
        conn.execute(
            "UPDATE meetings SET status = 'started_with_errors', started_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'starting'",
            (iso(current), iso(current), meeting_id),
        )
        conn.commit()
        logger.error("Recovery: could not get token for %s (id=%d): %s", alias, meeting_id, exc)
        return

    try:
        raw_live = _pexip.get_live_participants(alias, token)
    except Exception as exc:
        _pexip.release_control_token(alias, token)
        conn.execute(
            "UPDATE meetings SET status = 'started_with_errors', started_at = ?, updated_at = ? "
            "WHERE id = ? AND status = 'starting'",
            (iso(current), iso(current), meeting_id),
        )
        conn.commit()
        logger.error(
            "Recovery: could not fetch participants for %s (id=%d): %s", alias, meeting_id, exc
        )
        return

    live = normalize_live_participants(raw_live)

    endpoints = conn.execute(
        "SELECT * FROM meeting_endpoints WHERE meeting_id = ? AND status != 'dialed'",
        (meeting_id,),
    ).fetchall()

    all_ok = True
    for ep in endpoints:
        ep_alias = ep["endpoint_alias"]
        ep_display = ep["display_name"] or ep_alias
        try:
            if endpoint_matches_live(ep_alias, ep_display, live):
                conn.execute(
                    "UPDATE meeting_endpoints SET status = 'dialed' WHERE id = ?",
                    (ep["id"],),
                )
                conn.commit()
                logger.info(
                    "Recovery: %s already connected to %s; marked dialed, no redial issued",
                    ep_alias,
                    alias,
                )
            else:
                resp = _pexip.dial_endpoint_to_meeting(
                    alias, ep_alias, token, ep["role"] or "host"
                )
                conn.execute(
                    "UPDATE meeting_endpoints SET status = 'dialed', dial_response = ? WHERE id = ?",
                    (json.dumps(resp), ep["id"]),
                )
                conn.commit()
                logger.info("Recovery: %s not in conference %s; redialed", ep_alias, alias)
        except Exception as exc:
            all_ok = False
            conn.execute(
                "UPDATE meeting_endpoints SET status = 'error', dial_response = ? WHERE id = ?",
                (json.dumps({"error": str(exc)}), ep["id"]),
            )
            conn.commit()
            logger.warning(
                "Recovery: redial failed for %s in %s: %s", ep_alias, alias, exc
            )

    _pexip.release_control_token(alias, token)

    existing = conn.execute(
        "SELECT started_at FROM meetings WHERE id = ?", (meeting_id,)
    ).fetchone()
    started_at_val = (
        existing["started_at"] if existing and existing["started_at"] else iso(current)
    )

    final_status = "started" if all_ok else "started_with_errors"
    cur = conn.execute(
        "UPDATE meetings SET status = ?, started_at = ?, updated_at = ? "
        "WHERE id = ? AND status = 'starting'",
        (final_status, started_at_val, iso(current), meeting_id),
    )
    conn.commit()
    if cur.rowcount == 1:
        logger.info(
            "Recovery: meeting %s (id=%d) recovered as %s", alias, meeting_id, final_status
        )


def recover_stuck_meetings():
    current = now_utc()
    cutoff = current - timedelta(minutes=CRASH_RECOVERY_MINUTES)

    with closing(db()) as conn:
        stuck_starting = conn.execute(
            "SELECT id, meeting_alias, end_time FROM meetings "
            "WHERE status = 'starting' AND updated_at < ?",
            (iso(cutoff),),
        ).fetchall()

        for row in stuck_starting:
            end_dt = parse_iso(row["end_time"])
            if end_dt <= current:
                cur = conn.execute(
                    "UPDATE meetings SET status = 'ended_with_errors', ended_at = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'starting'",
                    (iso(current), iso(current), row["id"]),
                )
                conn.commit()
                if cur.rowcount == 1:
                    logger.warning(
                        "Meeting %s (id=%d): window passed during crash recovery; "
                        "marking ended_with_errors",
                        row["meeting_alias"],
                        row["id"],
                    )
            else:
                _recover_starting_meeting(conn, row, current)

        stuck_ending = conn.execute(
            "SELECT id, meeting_alias FROM meetings "
            "WHERE status = 'ending' AND updated_at < ?",
            (iso(cutoff),),
        ).fetchall()

        for row in stuck_ending:
            token = None
            try:
                token = _pexip.request_control_token(row["meeting_alias"])
                _pexip.disconnect_conference(row["meeting_alias"], token)
                cur = conn.execute(
                    "UPDATE meetings SET status = 'ended', ended_at = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'ending'",
                    (iso(current), iso(current), row["id"]),
                )
                conn.commit()
                if cur.rowcount == 1:
                    logger.info(
                        "Recovery: disconnect succeeded for %s (id=%d)",
                        row["meeting_alias"],
                        row["id"],
                    )
            except Exception as exc:
                conn.execute(
                    "UPDATE meetings SET status = 'ended_with_errors', ended_at = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'ending'",
                    (iso(current), iso(current), row["id"]),
                )
                conn.commit()
                logger.warning(
                    "Recovery: disconnect failed for %s (id=%d): %s",
                    row["meeting_alias"],
                    row["id"],
                    exc,
                )
            finally:
                if token:
                    _pexip.release_control_token(row["meeting_alias"], token)


def end_due_meetings():
    current = now_utc()
    with closing(db()) as conn:
        candidates = conn.execute(
            "SELECT id, meeting_alias FROM meetings "
            "WHERE status IN ('started', 'started_with_errors') AND end_time <= ? AND ended_at IS NULL",
            (iso(current),),
        ).fetchall()

        for row in candidates:
            meeting_id = row["id"]
            alias = row["meeting_alias"]

            cur = conn.execute(
                "UPDATE meetings SET status = 'ending', updated_at = ? "
                "WHERE id = ? AND status IN ('started', 'started_with_errors') AND ended_at IS NULL",
                (iso(current), meeting_id),
            )
            conn.commit()
            if cur.rowcount != 1:
                logger.debug(
                    "Meeting %d: end claim failed (rowcount=%d), skipping",
                    meeting_id,
                    cur.rowcount,
                )
                continue

            all_ok = True
            token = None
            try:
                token = _pexip.request_control_token(alias)
                _pexip.disconnect_conference(alias, token)
            except Exception as exc:
                all_ok = False
                logger.warning(
                    "End: disconnect failed for %s (id=%d): %s", alias, meeting_id, exc
                )
            finally:
                if token:
                    _pexip.release_control_token(alias, token)

            final_status = "ended" if all_ok else "ended_with_errors"
            conn.execute(
                "UPDATE meetings SET status = ?, ended_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'ending'",
                (final_status, iso(current), iso(current), meeting_id),
            )
            conn.execute(
                "UPDATE meeting_endpoints SET status = 'ended' WHERE meeting_id = ?",
                (meeting_id,),
            )
            conn.commit()
            logger.info("Meeting %s (id=%d) ended as %s", alias, meeting_id, final_status)


def start_due_meetings():
    current = now_utc()
    with closing(db()) as conn:
        candidates = conn.execute(
            "SELECT id, meeting_alias FROM meetings "
            "WHERE status = 'scheduled' AND start_time <= ? AND end_time > ?",
            (iso(current), iso(current)),
        ).fetchall()

        for row in candidates:
            meeting_id = row["id"]
            alias = row["meeting_alias"]

            cur = conn.execute(
                "UPDATE meetings SET status = 'starting', updated_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (iso(current), meeting_id),
            )
            conn.commit()
            if cur.rowcount != 1:
                logger.debug(
                    "Meeting %d: start claim failed (rowcount=%d), skipping",
                    meeting_id,
                    cur.rowcount,
                )
                continue

            endpoint_rows = conn.execute(
                "SELECT * FROM meeting_endpoints WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchall()

            all_ok = True
            token = None
            try:
                token = _pexip.request_control_token(alias)
                try:
                    _pexip.start_conference(alias, token)
                except Exception:
                    pass

                for ep in endpoint_rows:
                    try:
                        resp = _pexip.dial_endpoint_to_meeting(
                            alias, ep["endpoint_alias"], token, ep["role"] or "host"
                        )
                        conn.execute(
                            "UPDATE meeting_endpoints SET status = 'dialed', dial_response = ? "
                            "WHERE id = ?",
                            (json.dumps(resp), ep["id"]),
                        )
                        conn.commit()
                    except Exception as exc:
                        all_ok = False
                        conn.execute(
                            "UPDATE meeting_endpoints SET status = 'error', dial_response = ? "
                            "WHERE id = ?",
                            (json.dumps({"error": str(exc)}), ep["id"]),
                        )
                        conn.commit()
                        logger.warning(
                            "Start: dial failed for %s in %s: %s",
                            ep["endpoint_alias"],
                            alias,
                            exc,
                        )
            except Exception as exc:
                all_ok = False
                logger.error(
                    "Start: failed to get token for %s (id=%d): %s", alias, meeting_id, exc
                )
            finally:
                if token:
                    _pexip.release_control_token(alias, token)

            final_status = "started" if all_ok else "started_with_errors"
            cur = conn.execute(
                "UPDATE meetings SET status = ?, started_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'starting'",
                (final_status, iso(current), iso(current), meeting_id),
            )
            conn.commit()
            if cur.rowcount == 1:
                logger.info(
                    "Meeting %s (id=%d) started as %s", alias, meeting_id, final_status
                )


def scheduler_tick(worker_pid, worker_start_iso):
    try:
        with closing(db()) as conn:
            _write_heartbeat(conn, worker_pid, worker_start_iso)
    except Exception as exc:
        logger.error("Heartbeat write failed: %s", exc)

    try:
        recover_stuck_meetings()
    except Exception as exc:
        logger.error("recover_stuck_meetings failed: %s", exc)

    try:
        expire_missed_meetings()
    except Exception as exc:
        logger.error("expire_missed_meetings failed: %s", exc)

    try:
        end_due_meetings()
    except Exception as exc:
        logger.error("end_due_meetings failed: %s", exc)

    try:
        start_due_meetings()
    except Exception as exc:
        logger.error("start_due_meetings failed: %s", exc)
