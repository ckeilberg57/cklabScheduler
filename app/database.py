import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from app.config import Settings

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).isoformat()


def db():
    conn = sqlite3.connect(Settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with closing(db()) as conn:
        conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

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

            CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                last_seen   TEXT    NOT NULL,
                worker_pid  INTEGER NOT NULL,
                worker_start TEXT   NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT    NOT NULL,
                password_hash   TEXT,
                display_name    TEXT,
                role            TEXT    NOT NULL DEFAULT 'scheduler_user',
                auth_provider   TEXT    NOT NULL DEFAULT 'local',
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                last_login_at   TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_provider
                ON users(lower(username), auth_provider);

            CREATE TABLE IF NOT EXISTS auth_audit_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                event         TEXT    NOT NULL,
                username      TEXT,
                display_name  TEXT,
                auth_provider TEXT,
                ip_address    TEXT,
                success       INTEGER NOT NULL DEFAULT 1,
                detail        TEXT,
                created_at    TEXT    NOT NULL
            );
        """)
        conn.commit()


def log_auth_event(
    event,
    username=None,
    display_name=None,
    auth_provider=None,
    ip_address=None,
    success=True,
    detail=None,
):
    """
    Record an authentication or authorization event.
    Never logs passwords, tokens, or secrets.
    """
    log_msg = (
        f"AUTH event={event} username={username or 'unknown'} "
        f"provider={auth_provider or 'local'} success={success}"
    )
    if success:
        logger.info(log_msg)
    else:
        logger.warning(log_msg)

    try:
        with closing(db()) as conn:
            conn.execute(
                """
                INSERT INTO auth_audit_log
                    (event, username, display_name, auth_provider,
                     ip_address, success, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event, username, display_name, auth_provider,
                    ip_address, 1 if success else 0, detail, _now(),
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.error("Failed to write auth_audit_log: %s", exc)
