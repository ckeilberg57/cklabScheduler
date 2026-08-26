import sqlite3
from contextlib import closing

from app.config import Settings


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
        """)
        conn.commit()
