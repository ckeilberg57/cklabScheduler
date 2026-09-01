import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from flask_login import UserMixin

from app.database import db


def _now():
    return datetime.now(timezone.utc).isoformat()


class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.display_name = row["display_name"] or row["username"]
        self.role = row["role"]
        self.auth_provider = row["auth_provider"]
        self._enabled = bool(row["enabled"])

    @property
    def is_active(self):
        return self._enabled

    def has_role(self, role):
        """Return True if the user holds the given role or higher."""
        if self.role == "administrator":
            return True
        return self.role == role

    def is_administrator(self):
        return self.role == "administrator"


def get_user_by_id(user_id):
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return User(row)


def get_user_by_username(username, auth_provider="local"):
    with closing(db()) as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE lower(username) = lower(?) AND auth_provider = ?",
            (username, auth_provider),
        ).fetchone()
        if row is None:
            return None
        return User(row)


def get_user_row_by_username(username, auth_provider="local"):
    """Return the raw sqlite3.Row (includes password_hash for local auth)."""
    with closing(db()) as conn:
        return conn.execute(
            "SELECT * FROM users WHERE lower(username) = lower(?) AND auth_provider = ?",
            (username, auth_provider),
        ).fetchone()


def update_last_login(user_id):
    with closing(db()) as conn:
        conn.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?",
            (_now(), user_id),
        )
        conn.commit()


def create_local_user(username, password_hash, display_name=None, role="scheduler_user"):
    """Create a new local user. Raises ValueError on duplicate username."""
    now = _now()
    with closing(db()) as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, display_name, role, auth_provider,
                     enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'local', 1, ?, ?)
                """,
                (username, password_hash, display_name or username, role, now, now),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            raise ValueError(
                f"Username '{username}' already exists for local authentication."
            )


def upsert_entra_user(username, display_name, role):
    """Create or update an Entra user record on login. Returns User."""
    now = _now()
    with closing(db()) as conn:
        existing = conn.execute(
            "SELECT * FROM users WHERE lower(username) = lower(?) AND auth_provider = 'entra'",
            (username,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE users
                SET display_name = ?, role = ?, updated_at = ?, last_login_at = ?
                WHERE id = ?
                """,
                (display_name, role, now, now, existing["id"]),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (existing["id"],)
            ).fetchone()
        else:
            cur = conn.execute(
                """
                INSERT INTO users
                    (username, password_hash, display_name, role, auth_provider,
                     enabled, created_at, updated_at, last_login_at)
                VALUES (?, NULL, ?, ?, 'entra', 1, ?, ?, ?)
                """,
                (username, display_name, role, now, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM users WHERE id = ?", (cur.lastrowid,)
            ).fetchone()

        return User(row)


def list_local_users():
    """Return all local users as a list of sqlite3.Row objects."""
    with closing(db()) as conn:
        return conn.execute(
            "SELECT * FROM users WHERE auth_provider = 'local' ORDER BY username"
        ).fetchall()


def set_user_enabled(username, enabled):
    """Enable or disable a local user. Returns True if updated."""
    with closing(db()) as conn:
        cur = conn.execute(
            """
            UPDATE users SET enabled = ?, updated_at = ?
            WHERE lower(username) = lower(?) AND auth_provider = 'local'
            """,
            (1 if enabled else 0, _now(), username),
        )
        conn.commit()
        return cur.rowcount > 0


def set_user_password(username, password_hash):
    """Update a local user's password hash. Returns True if updated."""
    with closing(db()) as conn:
        cur = conn.execute(
            """
            UPDATE users SET password_hash = ?, updated_at = ?
            WHERE lower(username) = lower(?) AND auth_provider = 'local'
            """,
            (password_hash, _now(), username),
        )
        conn.commit()
        return cur.rowcount > 0


def set_user_role(username, role):
    """Update a local user's role. Returns True if updated."""
    with closing(db()) as conn:
        cur = conn.execute(
            """
            UPDATE users SET role = ?, updated_at = ?
            WHERE lower(username) = lower(?) AND auth_provider = 'local'
            """,
            (role, _now(), username),
        )
        conn.commit()
        return cur.rowcount > 0
