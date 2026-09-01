from contextlib import closing
from datetime import timedelta
from unittest.mock import patch

from app.config import Settings
from app.database import db
from app.meeting_utils import iso, now_utc
from app.routes.health import HEARTBEAT_STALE_SECONDS


def _write_heartbeat(test_db, last_seen_iso, worker_pid=1234):
    with patch.object(Settings, "DB_PATH", test_db):
        with closing(db()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_heartbeat "
                "(id, last_seen, worker_pid, worker_start) VALUES (1, ?, ?, ?)",
                (last_seen_iso, worker_pid, iso(now_utc())),
            )
            conn.commit()


def make_app(test_db):
    from unittest.mock import MagicMock
    with patch.object(Settings, "DB_PATH", test_db), \
         patch.object(Settings, "REG_STATUS_HOST", "pexip.example.com"), \
         patch.object(Settings, "COMMAND_HOST", "edge.example.com"), \
         patch.object(Settings, "API_USER", "user"), \
         patch.object(Settings, "API_PASS", "pass"), \
         patch.object(Settings, "SECRET_KEY", "testsecret"), \
         patch.object(Settings, "O365_ENABLED", False), \
         patch.object(Settings, "LOCAL_AUTH_ENABLED", True), \
         patch.object(Settings, "ENTRA_ENABLED", False), \
         patch.object(Settings, "SESSION_COOKIE_SECURE", False), \
         patch("app.PexipAPI", return_value=MagicMock()):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        return app


class TestHealthHeartbeat:
    def test_never_started_returns_false(self, test_db):
        with patch.object(Settings, "DB_PATH", test_db):
            app = make_app(test_db)
            with app.test_client() as client:
                with patch.object(Settings, "DB_PATH", test_db):
                    resp = client.get("/api/health")
            data = resp.get_json()
            assert data["scheduler_worker"]["ok"] is False
            assert data["scheduler_worker"]["status"] == "never_started"
            assert resp.status_code == 500

    def test_fresh_heartbeat_returns_ok(self, test_db):
        _write_heartbeat(test_db, iso(now_utc() - timedelta(seconds=5)))
        with patch.object(Settings, "DB_PATH", test_db):
            app = make_app(test_db)
            with app.test_client() as client:
                with patch.object(Settings, "DB_PATH", test_db):
                    resp = client.get("/api/health")
            data = resp.get_json()
            assert data["scheduler_worker"]["ok"] is True
            assert data["scheduler_worker"]["last_heartbeat_seconds_ago"] < HEARTBEAT_STALE_SECONDS
            assert resp.status_code == 200

    def test_stale_heartbeat_returns_false(self, test_db):
        stale_time = iso(now_utc() - timedelta(seconds=HEARTBEAT_STALE_SECONDS + 10))
        _write_heartbeat(test_db, stale_time)
        with patch.object(Settings, "DB_PATH", test_db):
            app = make_app(test_db)
            with app.test_client() as client:
                with patch.object(Settings, "DB_PATH", test_db):
                    resp = client.get("/api/health")
            data = resp.get_json()
            assert data["scheduler_worker"]["ok"] is False
            assert data["scheduler_worker"]["status"] == "stale"
            assert data["scheduler_worker"]["last_heartbeat_seconds_ago"] >= HEARTBEAT_STALE_SECONDS
            assert resp.status_code == 500

    def test_health_response_has_expected_fields(self, test_db):
        _write_heartbeat(test_db, iso(now_utc() - timedelta(seconds=5)))
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/health")
        data = resp.get_json()
        assert "ok" in data
        assert "service" in data
        assert "version" in data
        assert "database" in data
        assert "pexip" in data
        assert "o365" in data
        assert "scheduler_worker" in data

    def test_health_does_not_expose_hostnames(self, test_db):
        _write_heartbeat(test_db, iso(now_utc() - timedelta(seconds=5)))
        app = make_app(test_db)
        with app.test_client() as client:
            with patch.object(Settings, "DB_PATH", test_db):
                resp = client.get("/api/health")
        body = resp.get_data(as_text=True)
        assert "pexip.example.com" not in body
        assert "edge.example.com" not in body
