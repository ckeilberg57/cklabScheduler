import pytest
from contextlib import closing
from unittest.mock import patch

from app.config import Settings
from app.database import db, init_db
from app.meeting_utils import iso, now_utc
from datetime import timedelta


@pytest.fixture
def test_db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def test_db(test_db_path):
    with patch.object(Settings, "DB_PATH", test_db_path):
        init_db()
        yield test_db_path


def insert_meeting(conn, **kwargs):
    defaults = {
        "title": "Test Meeting",
        "meeting_alias": "doctest1234567890",
        "start_time": iso(now_utc() - timedelta(minutes=5)),
        "end_time": iso(now_utc() + timedelta(hours=1)),
        "status": "scheduled",
        "started_at": None,
        "ended_at": None,
        "created_at": iso(now_utc()),
        "updated_at": iso(now_utc()),
        "notes": "",
    }
    defaults.update(kwargs)
    cur = conn.execute(
        """
        INSERT INTO meetings
            (title, meeting_alias, start_time, end_time, status,
             started_at, ended_at, created_at, updated_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            defaults["title"], defaults["meeting_alias"],
            defaults["start_time"], defaults["end_time"], defaults["status"],
            defaults["started_at"], defaults["ended_at"],
            defaults["created_at"], defaults["updated_at"], defaults["notes"],
        ),
    )
    conn.commit()
    return cur.lastrowid


def insert_endpoint(conn, meeting_id, **kwargs):
    defaults = {
        "endpoint_alias": "ep@example.com",
        "display_name": "Test Endpoint",
        "role": "host",
        "status": "scheduled",
    }
    defaults.update(kwargs)
    cur = conn.execute(
        """
        INSERT INTO meeting_endpoints (meeting_id, endpoint_alias, display_name, role, status)
        VALUES (?, ?, ?, ?, ?)
        """,
        (meeting_id, defaults["endpoint_alias"], defaults["display_name"],
         defaults["role"], defaults["status"]),
    )
    conn.commit()
    return cur.lastrowid
