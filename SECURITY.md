# Security

This document describes the security model for cklabScheduler and guidance for operators deploying it.

---

## Authentication

cklabScheduler supports two authentication methods, independently enabled:

| Method | Description |
|---|---|
| **Local accounts** | SQLite-stored users with PBKDF2-SHA256 hashed passwords. Enabled by default. |
| **Microsoft Entra ID** | OIDC Authorization Code Flow via MSAL. Single-tenant. Optional. |

At least one method must be enabled at all times. The installer enforces this — it will not write a configuration with both methods disabled. The `Settings.validate_web()` startup check also rejects configurations where both are disabled.

**Roles:**

| Role | Access |
|---|---|
| `administrator` | Full access — satisfies any `has_role()` check |
| `scheduler_user` | Standard access — meeting creation and management |

Entra app roles map as follows: `Scheduler.Administrator` → `administrator`, `Scheduler.User` → `scheduler_user`. Users with no assigned role are denied access after authentication.

---

## Local account password security

- Passwords are hashed with **Werkzeug `generate_password_hash`** using the `pbkdf2:sha256:600000` scheme (PBKDF2-HMAC-SHA256 with 600,000 iterations).
- Minimum password length: **12 characters**, enforced at creation and reset.
- Passwords are **never stored in plaintext**, never logged, never displayed after entry, and never passed as command-line arguments.
- The installer collects the initial admin password using `read -rs` (no terminal echo) and passes it to Python via an environment variable — not via `argv`.
- The `manage_users` CLI uses `getpass.getpass()` (reads from `/dev/tty`, not echoed).

---

## Session security

Sessions are signed with `SECRET_KEY` (generated automatically by the installer via `openssl rand -hex 32`).

| Cookie attribute | Value |
|---|---|
| `Secure` | `true` in production (HTTPS only) |
| `HttpOnly` | `true` — JavaScript cannot access the session cookie |
| `SameSite` | `Lax` — allows OAuth redirect while protecting against CSRF |
| Lifetime | 8 hours from last request |

**Session fixation prevention:** `session.clear()` is called before `login_user()` on every login — this invalidates any session state accumulated before authentication.

**Open redirect protection:** The `?next=` parameter on the login URL is validated with `urlparse` — only same-origin paths are accepted; any URL with a netloc (host) component is rejected.

---

## CSRF protection

Flask-WTF (`CSRFProtect`) is applied globally. All state-changing requests must include a valid CSRF token.

- **HTML forms**: a hidden `{{ csrf_token() }}` field is included in all forms.
- **JSON API calls** (AJAX): the token is embedded in a `<meta name="csrf-token">` tag and sent as an `X-CSRFToken` request header.
- The CSRF token is tied to the session and expires with it.

---

## Credential storage

All runtime secrets are stored in `/etc/cklabScheduler/cklabScheduler.env`.

| Setting | Notes |
|---|---|
| `MGMT_PASS` | Pexip Management API password |
| `SECRET_KEY` | Flask session signing key (generated automatically; never prompted) |
| `O365_CLIENT_SECRET` | Azure AD client secret for Microsoft 365 integration |
| `ENTRA_CLIENT_SECRET` | Microsoft Entra client secret (when Entra auth is enabled) |

**File permissions:** `640 root:cklabscheduler` — readable only by root and the `cklabscheduler` service account.

**`SECRET_KEY` generation:** The installer generates this value with `openssl rand -hex 32` and writes it directly to the env file. It is never echoed to the terminal and is never prompted from the operator.

**Passwords at install time:** All secrets are collected via prompts that suppress terminal echo (`read -rs`). They are never written to shell history or log files.

---

## Audit logging

Authentication events are written to two destinations:

1. **Python logger** (`app.auth`): appears in `journalctl -u cklab-scheduler-web`.
2. **`auth_audit_log` SQLite table**: persists across restarts; queryable via `sqlite3 /var/lib/cklabScheduler/scheduler.db`.

Events logged: login success, login failure, logout, account disabled, invalid password, Entra auth success/failure, role assignment changes.

**What is never logged:** passwords, password hashes, client secrets, authorization codes, access tokens, ID tokens, refresh tokens, session cookies.

---

## Service account

The application runs as a dedicated, login-disabled service account (`cklabscheduler`, shell `/usr/sbin/nologin`). This account:
- owns the database directory (`/var/lib/cklabScheduler/`, mode 750)
- has read access to the application directory (`/opt/cklabScheduler/`, root:cklabscheduler 750)
- has read access to the configuration file (`/etc/cklabScheduler/cklabScheduler.env`, mode 640)

The web and worker processes run as this account under systemd.

---

## Network exposure

**Gunicorn listens on `127.0.0.1:5080` only.** It is not accessible from the network directly.

**Apache terminates TLS** and reverse-proxies `/cklabScheduler/` to Gunicorn. All external traffic goes through Apache, which enforces HTTPS.

The installer supports:
- Let's Encrypt (certbot) for public-facing deployments
- Self-signed certificates for internal/lab use
- Existing certificates (operator-provided paths)

---

## Pexip API account

The scheduler uses a Pexip Management API account to:
- List registered endpoints (status API, read-only)
- Dial and disconnect participants (command API, write)

**Recommended least-privilege configuration:**
- Create a dedicated Pexip administrator account for the scheduler
- Grant only the permissions required for registration status reads and participant dial/disconnect
- Do not use the Pexip default `admin` account
- Rotate the password on a schedule consistent with your organisation's policy

---

## Microsoft 365 credentials

When O365 integration is enabled, the scheduler uses an Azure AD application (client credentials flow) to send calendar invitations. Recommended configuration:
- Register a dedicated Azure AD application for the scheduler
- Grant only `Calendars.ReadWrite` and `Mail.Send` (or `Mail.Send.Shared`) on the specific mailbox
- Use a client secret with the shortest expiry your workflow permits, and rotate it before expiry
- Do not grant tenant-wide permissions beyond what is required

The `O365_CLIENT_SECRET` is stored in the env file with `640` permissions and is never logged or exposed in API responses.

---

## What must never be committed to version control

- Real `.env` files or any file containing live credentials
- The `cklabScheduler.env` configuration file from any deployment
- Private TLS key files (`*.key`, `*.pem`, `*.p12`)
- SQLite database files (`*.db`)
- Old monolithic `app.py` from the original build if it contains hardcoded credentials

The `.gitignore` in this repository excludes `.env`, `.env.*`, `*.db`, `*.key`, `*.pem`, and related patterns. These patterns are listed in `.gitignore` but **gitignore only prevents future tracking — it does not remove files that were already committed.** Before any push, verify with `git ls-files` that no sensitive files are tracked.

---

## Health endpoint

`GET /api/health` is a **public endpoint** (no authentication required). It returns operational status including an `authentication` field showing which methods are enabled:

```json
"authentication": { "local_enabled": true, "entra_enabled": false }
```

It does **not** expose:
- Pexip hostnames
- API credentials
- Database paths
- Secret keys
- Tenant IDs, client IDs, or any Entra configuration details

---

## Reporting a security concern

If you identify a security vulnerability or have a concern about credential handling, contact the internal security team directly. Do not open a public issue.
