import sqlite3
from contextlib import closing
from unittest.mock import patch

from app.config import Settings
from app.database import db, init_db


def test_init_db_creates_all_tables(test_db_path):
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        with closing(db()) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "meetings" in tables
        assert "meeting_endpoints" in tables
        assert "meeting_invitees" in tables
        assert "scheduler_heartbeat" in tables


def test_init_db_is_idempotent(test_db_path):
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        init_db()
        with closing(db()) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "meetings" in tables
        assert "scheduler_heartbeat" in tables


def test_db_helper_sets_wal_mode(test_db_path):
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        with closing(db()) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


def test_db_helper_enforces_foreign_keys(test_db_path):
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        with closing(db()) as conn:
            fk_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_enabled == 1


def test_scheduler_heartbeat_single_row_constraint(test_db_path):
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        with closing(db()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_heartbeat (id, last_seen, worker_pid, worker_start) "
                "VALUES (1, '2024-01-01T00:00:00+00:00', 1234, '2024-01-01T00:00:00+00:00')"
            )
            conn.commit()
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_heartbeat (id, last_seen, worker_pid, worker_start) "
                "VALUES (1, '2024-01-01T00:00:01+00:00', 5678, '2024-01-01T00:00:00+00:00')"
            )
            conn.commit()
            count = conn.execute(
                "SELECT COUNT(*) FROM scheduler_heartbeat"
            ).fetchone()[0]
        assert count == 1


def test_scheduler_heartbeat_rejects_id_not_one(test_db_path):
    import pytest
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        with closing(db()) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO scheduler_heartbeat (id, last_seen, worker_pid, worker_start) "
                    "VALUES (2, '2024-01-01T00:00:00+00:00', 1, '2024-01-01T00:00:00+00:00')"
                )
                conn.commit()
