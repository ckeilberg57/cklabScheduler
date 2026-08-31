# Security

This document describes the security model for cklabScheduler and guidance for operators deploying it.

---

## Credential storage

All runtime secrets are stored in `/etc/cklabScheduler/cklabScheduler.env`.

| Setting | Notes |
|---|---|
| `MGMT_PASS` | Pexip Management API password |
| `SECRET_KEY` | Flask session signing key (generated automatically; never prompted) |
| `O365_CLIENT_SECRET` | Azure AD client secret for Microsoft 365 integration |

**File permissions:** `640 root:cklabscheduler` — readable only by root and the `cklabscheduler` service account.

**`SECRET_KEY` generation:** The installer generates this value with `openssl rand -hex 32` and writes it directly to the env file. It is never echoed to the terminal and is never prompted from the operator.

**Passwords at install time:** All secrets are collected via prompts that suppress terminal echo (`read -rs`). They are never written to shell history or log files.

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

`GET /api/health` returns operational status but does not expose:
- Pexip hostnames
- API credentials
- Database paths
- Secret keys

---

## Reporting a security concern

If you identify a security vulnerability or have a concern about credential handling, contact the internal security team directly. Do not open a public issue.
