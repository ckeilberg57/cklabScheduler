from contextlib import closing
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.database import db
from app.meeting_utils import iso, now_utc
from tests.conftest import insert_endpoint, insert_meeting


def _run_with_db(test_db_path, func):
    with patch.object(Settings, "DB_PATH", test_db_path):
        func()


class TestAtomicClaims:
    def test_start_claims_per_meeting_and_marks_started(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="scheduled",
                    start_time=iso(now_utc() - timedelta(minutes=1)),
                    end_time=iso(now_utc() + timedelta(hours=1)),
                )
                insert_endpoint(conn, mid)

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.start_conference.return_value = {}
            mock_pexip.dial_endpoint_to_meeting.return_value = {"result": "ok"}

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import start_due_meetings
                start_due_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
                ep = conn.execute(
                    "SELECT status FROM meeting_endpoints WHERE meeting_id = ?", (mid,)
                ).fetchone()
            assert row["status"] == "started"
            assert ep["status"] == "dialed"

    def test_start_skips_meeting_already_claimed(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="starting",
                    start_time=iso(now_utc() - timedelta(minutes=1)),
                    end_time=iso(now_utc() + timedelta(hours=1)),
                )

            mock_pexip = MagicMock()
            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import start_due_meetings
                start_due_meetings()

            mock_pexip.request_control_token.assert_not_called()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "starting"

    def test_end_claims_per_meeting_and_marks_ended(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="started",
                    start_time=iso(now_utc() - timedelta(hours=2)),
                    end_time=iso(now_utc() - timedelta(minutes=1)),
                    started_at=iso(now_utc() - timedelta(hours=2)),
                )
                insert_endpoint(conn, mid, status="dialed")

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.disconnect_conference.return_value = {}

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import end_due_meetings
                end_due_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
                ep = conn.execute(
                    "SELECT status FROM meeting_endpoints WHERE meeting_id = ?", (mid,)
                ).fetchone()
            assert row["status"] == "ended"
            assert ep["status"] == "ended"

    def test_end_marks_ended_with_errors_on_disconnect_failure(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="started",
                    start_time=iso(now_utc() - timedelta(hours=2)),
                    end_time=iso(now_utc() - timedelta(minutes=1)),
                    started_at=iso(now_utc() - timedelta(hours=2)),
                )

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.disconnect_conference.side_effect = RuntimeError("disconnect failed")

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import end_due_meetings
                end_due_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "ended_with_errors"


class TestExpireMissedMeetings:
    def test_marks_scheduled_meeting_ended_with_errors_when_window_elapsed(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="scheduled",
                    start_time=iso(now_utc() - timedelta(hours=2)),
                    end_time=iso(now_utc() - timedelta(minutes=1)),
                )

            mock_pexip = MagicMock()
            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import expire_missed_meetings
                expire_missed_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT * FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "ended_with_errors"
            assert row["ended_at"] is not None
            assert row["started_at"] is None

    def test_does_not_touch_meeting_still_in_window(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="scheduled",
                    start_time=iso(now_utc() - timedelta(minutes=5)),
                    end_time=iso(now_utc() + timedelta(hours=1)),
                )

            from app.scheduler_jobs import expire_missed_meetings
            expire_missed_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "scheduled"

    def test_does_not_make_pexip_calls(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                insert_meeting(
                    conn,
                    status="scheduled",
                    start_time=iso(now_utc() - timedelta(hours=2)),
                    end_time=iso(now_utc() - timedelta(minutes=1)),
                )

            mock_pexip = MagicMock()
            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import expire_missed_meetings
                expire_missed_meetings()

            mock_pexip.request_control_token.assert_not_called()
            mock_pexip.dial_endpoint_to_meeting.assert_not_called()


class TestRecoverStuckStarting:
    def test_redialing_absent_endpoint(self, test_db):
        cutoff_time = iso(now_utc() - timedelta(minutes=5))
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="starting",
                    start_time=iso(now_utc() - timedelta(minutes=10)),
                    end_time=iso(now_utc() + timedelta(hours=1)),
                    updated_at=cutoff_time,
                )
                insert_endpoint(conn, mid, status="scheduled")

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.get_live_participants.return_value = []
            mock_pexip.dial_endpoint_to_meeting.return_value = {"result": "ok"}

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import recover_stuck_meetings
                recover_stuck_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
                ep = conn.execute(
                    "SELECT status FROM meeting_endpoints WHERE meeting_id = ?", (mid,)
                ).fetchone()
            assert row["status"] == "started"
            assert ep["status"] == "dialed"
            mock_pexip.dial_endpoint_to_meeting.assert_called_once()

    def test_marks_already_live_endpoint_without_redialing(self, test_db):
        cutoff_time = iso(now_utc() - timedelta(minutes=5))
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="starting",
                    start_time=iso(now_utc() - timedelta(minutes=10)),
                    end_time=iso(now_utc() + timedelta(hours=1)),
                    updated_at=cutoff_time,
                )
                insert_endpoint(conn, mid, endpoint_alias="ep@example.com", status="scheduled")

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.get_live_participants.return_value = [
                {"remote_alias": "ep@example.com", "display_name": "Test Endpoint"}
            ]

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import recover_stuck_meetings
                recover_stuck_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
                ep = conn.execute(
                    "SELECT status FROM meeting_endpoints WHERE meeting_id = ?", (mid,)
                ).fetchone()
            assert row["status"] == "started"
            assert ep["status"] == "dialed"
            mock_pexip.dial_endpoint_to_meeting.assert_not_called()

    def test_preserves_existing_started_at(self, test_db):
        cutoff_time = iso(now_utc() - timedelta(minutes=5))
        original_started_at = iso(now_utc() - timedelta(minutes=9))
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="starting",
                    start_time=iso(now_utc() - timedelta(minutes=10)),
                    end_time=iso(now_utc() + timedelta(hours=1)),
                    updated_at=cutoff_time,
                    started_at=original_started_at,
                )

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.get_live_participants.return_value = []

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import recover_stuck_meetings
                recover_stuck_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT started_at FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["started_at"] == original_started_at

    def test_window_passed_during_recovery_marks_ended_with_errors(self, test_db):
        cutoff_time = iso(now_utc() - timedelta(minutes=5))
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="starting",
                    start_time=iso(now_utc() - timedelta(hours=2)),
                    end_time=iso(now_utc() - timedelta(minutes=1)),
                    updated_at=cutoff_time,
                )

            mock_pexip = MagicMock()
            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import recover_stuck_meetings
                recover_stuck_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "ended_with_errors"
            mock_pexip.request_control_token.assert_not_called()

    def test_does_not_recover_recently_updated_starting(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="starting",
                    start_time=iso(now_utc() - timedelta(minutes=1)),
                    end_time=iso(now_utc() + timedelta(hours=1)),
                    updated_at=iso(now_utc()),
                )

            mock_pexip = MagicMock()
            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import recover_stuck_meetings
                recover_stuck_meetings()

            mock_pexip.request_control_token.assert_not_called()
            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "starting"


class TestRecoverStuckEnding:
    def test_retry_disconnect_succeeds(self, test_db):
        cutoff_time = iso(now_utc() - timedelta(minutes=5))
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="ending",
                    start_time=iso(now_utc() - timedelta(hours=2)),
                    end_time=iso(now_utc() - timedelta(minutes=1)),
                    started_at=iso(now_utc() - timedelta(hours=2)),
                    updated_at=cutoff_time,
                )

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.disconnect_conference.return_value = {}

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import recover_stuck_meetings
                recover_stuck_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "ended"

    def test_retry_disconnect_fails_marks_ended_with_errors(self, test_db):
        cutoff_time = iso(now_utc() - timedelta(minutes=5))
        with patch.object(Settings, "DB_PATH", test_db):
            with closing(db()) as conn:
                mid = insert_meeting(
                    conn,
                    status="ending",
                    start_time=iso(now_utc() - timedelta(hours=2)),
                    end_time=iso(now_utc() - timedelta(minutes=1)),
                    started_at=iso(now_utc() - timedelta(hours=2)),
                    updated_at=cutoff_time,
                )

            mock_pexip = MagicMock()
            mock_pexip.request_control_token.return_value = "tok"
            mock_pexip.disconnect_conference.side_effect = RuntimeError("gone")

            with patch("app.scheduler_jobs._pexip", mock_pexip):
                from app.scheduler_jobs import recover_stuck_meetings
                recover_stuck_meetings()

            with closing(db()) as conn:
                row = conn.execute("SELECT status FROM meetings WHERE id = ?", (mid,)).fetchone()
            assert row["status"] == "ended_with_errors"
