"""
cklabScheduler local user management utility.

Usage
-----
  python -m app.manage_users list
  python -m app.manage_users create [--role administrator|scheduler_user] <username>
  python -m app.manage_users disable <username>
  python -m app.manage_users enable <username>
  python -m app.manage_users reset-password <username>
  python -m app.manage_users change-role <username> <role>

Passwords are prompted securely and never passed as command-line arguments.

Minimum password length: 12 characters.

In production, set DB_PATH in the environment or the system config file is
loaded automatically from /etc/cklabScheduler/cklabScheduler.env.
"""
import argparse
import getpass
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load production config before importing app modules.
_system_env = Path("/etc/cklabScheduler/cklabScheduler.env")
_dev_env = Path(__file__).resolve().parent.parent / ".env"
if _system_env.exists():
    load_dotenv(_system_env, override=False)
elif _dev_env.exists():
    load_dotenv(_dev_env, override=False)

from app.auth.local import (  # noqa: E402
    MIN_PASSWORD_LENGTH,
    hash_password,
    validate_password_strength,
)
from app.auth.models import (  # noqa: E402
    create_local_user,
    list_local_users,
    set_user_enabled,
    set_user_password,
    set_user_role,
)
from app.database import init_db, log_auth_event  # noqa: E402

VALID_ROLES = ("administrator", "scheduler_user")


def _prompt_new_password():
    """Prompt for a new password twice; return the confirmed password."""
    while True:
        pw = getpass.getpass(f"  Password (min {MIN_PASSWORD_LENGTH} chars): ")
        try:
            validate_password_strength(pw)
        except ValueError as exc:
            print(f"  Error: {exc}", file=sys.stderr)
            continue
        pw2 = getpass.getpass("  Confirm password: ")
        if pw != pw2:
            print("  Passwords do not match. Please try again.", file=sys.stderr)
            continue
        return pw


def cmd_list():
    users = list_local_users()
    if not users:
        print("No local users found.")
        return
    fmt = "{:<4}  {:<20}  {:<20}  {:<15}  {}"
    print(fmt.format("ID", "Username", "Display Name", "Role", "Enabled"))
    print("-" * 78)
    for u in users:
        print(fmt.format(
            u["id"],
            u["username"],
            u["display_name"] or "",
            u["role"],
            "yes" if u["enabled"] else "no",
        ))


def cmd_create(username, role):
    """Create a new local user, prompting securely for password."""
    if role not in VALID_ROLES:
        print(f"Error: role must be one of: {', '.join(VALID_ROLES)}", file=sys.stderr)
        sys.exit(1)
    print(f"Creating local user '{username}' with role '{role}'.")
    password = _prompt_new_password()
    password_hash = hash_password(password)
    try:
        user_id = create_local_user(username, password_hash, role=role)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    log_auth_event("user_created", username=username, auth_provider="local", detail=f"role={role}")
    print(f"User '{username}' created successfully (id={user_id}).")


def cmd_disable(username):
    if not set_user_enabled(username, False):
        print(f"Error: local user '{username}' not found.", file=sys.stderr)
        sys.exit(1)
    log_auth_event("user_disabled", username=username, auth_provider="local")
    print(f"User '{username}' disabled.")


def cmd_enable(username):
    if not set_user_enabled(username, True):
        print(f"Error: local user '{username}' not found.", file=sys.stderr)
        sys.exit(1)
    log_auth_event("user_enabled", username=username, auth_provider="local")
    print(f"User '{username}' enabled.")


def cmd_reset_password(username):
    print(f"Resetting password for '{username}'.")
    password = _prompt_new_password()
    password_hash = hash_password(password)
    if not set_user_password(username, password_hash):
        print(f"Error: local user '{username}' not found.", file=sys.stderr)
        sys.exit(1)
    log_auth_event("password_reset", username=username, auth_provider="local")
    print(f"Password for '{username}' updated.")


def cmd_change_role(username, role):
    if role not in VALID_ROLES:
        print(f"Error: role must be one of: {', '.join(VALID_ROLES)}", file=sys.stderr)
        sys.exit(1)
    if not set_user_role(username, role):
        print(f"Error: local user '{username}' not found.", file=sys.stderr)
        sys.exit(1)
    log_auth_event("role_changed", username=username, auth_provider="local", detail=f"new_role={role}")
    print(f"Role for '{username}' changed to '{role}'.")


def main():
    init_db()

    parser = argparse.ArgumentParser(
        description="cklabScheduler local user management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List all local users")

    p_create = sub.add_parser("create", help="Create a new local user")
    p_create.add_argument("username", help="Username (case-insensitive)")
    p_create.add_argument(
        "--role",
        default="scheduler_user",
        choices=VALID_ROLES,
        help="Role to assign (default: scheduler_user)",
    )

    p_disable = sub.add_parser("disable", help="Disable a local user")
    p_disable.add_argument("username")

    p_enable = sub.add_parser("enable", help="Enable a local user")
    p_enable.add_argument("username")

    p_reset = sub.add_parser("reset-password", help="Reset a local user's password")
    p_reset.add_argument("username")

    p_role = sub.add_parser("change-role", help="Change a local user's role")
    p_role.add_argument("username")
    p_role.add_argument("role", choices=VALID_ROLES)

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "create":
        cmd_create(args.username, args.role)
    elif args.command == "disable":
        cmd_disable(args.username)
    elif args.command == "enable":
        cmd_enable(args.username)
    elif args.command == "reset-password":
        cmd_reset_password(args.username)
    elif args.command == "change-role":
        cmd_change_role(args.username, args.role)


if __name__ == "__main__":
    main()
