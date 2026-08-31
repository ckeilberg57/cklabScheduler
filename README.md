# cklabScheduler

A production-grade Pexip meeting scheduler built on Flask and APScheduler. It lets operators book meetings with registered Pexip endpoints, automatically dials those endpoints at the scheduled start time, and disconnects them at the scheduled end time.

---

## What it does

- Books meetings against Pexip VMR aliases (`doc<16 alphanumeric>`)
- Fetches currently-registered Pexip endpoints from the Management Status API and shows them in the booking UI
- Dials selected endpoints into the VMR at meeting start via the Pexip Command API
- Disconnects endpoints at meeting end
- Shows a live day-timeline with status colours (scheduled → about-to-start → started → ended)
- Lets operators extend, end early, or inspect live meetings
- Optionally sends Microsoft 365 calendar invitations to participants

---

<img width="1470" height="828" alt="image" src="https://github.com/user-attachments/assets/10e77904-6d37-4d42-bd03-9ca56864f19a" />

## Architecture

### Web request path

```
Browser
   |
 HTTPS (TLS terminated at Apache)
   |
Apache  (/cklabScheduler/ → http://127.0.0.1:5080/cklabScheduler/)
   |
Gunicorn  (SCRIPT_NAME=/cklabScheduler, workers=2)
   |
Flask application  (Blueprint routes under /api/ and /)
   |
SQLite  (/var/lib/cklabScheduler/scheduler.db)
```

### Scheduler worker (separate process)

```
cklab-scheduler-worker  (systemd unit)
      |
      +-- APScheduler (BackgroundScheduler, 10-second tick)
      |       |
      |       +-- start_due_meetings()   → Pexip Command API
      |       +-- end_due_meetings()     → Pexip Command API
      |       +-- expire_missed_meetings()
      |       +-- recover_stuck_meetings()
      |
      +-- SQLite  (shared database, WAL mode)
      |
      +-- Heartbeat row  (read by /api/health)
```

**Why APScheduler runs only in the worker, not inside Gunicorn:**
Gunicorn spawns multiple worker processes. If APScheduler ran inside Gunicorn, every worker process would run its own scheduler instance, causing duplicate dial-outs and disconnect calls for every meeting. The standalone worker process is the single source of truth for all scheduling actions. The web process only reads and writes meeting state; it never directly drives Pexip calls.

---

## Supported platform

- **Ubuntu 24.04 LTS** (production-supported)
- Python 3.12 (installed by `install.sh`)
- Apache 2 with `mod_proxy`, `mod_proxy_http`, `mod_ssl`

---

## Application directory layout (installed)

```
/opt/cklabScheduler/            root:cklabscheduler 750  — application code
├── app/
│   ├── __init__.py             Flask factory (create_app)
│   ├── config.py               Settings class, per-process startup validation
│   ├── database.py             init_db(), db() helper, WAL + FK pragmas
│   ├── email_service.py        Microsoft 365 Graph API integration
│   ├── meeting_utils.py        shared helpers (alias, time, participant matching)
│   ├── pexip.py                PexipAPI class (status + command endpoints)
│   ├── scheduler_jobs.py       tick functions — imported only by worker.py
│   ├── routes/
│   │   ├── endpoints.py        GET /api/endpoints, GET /api/config
│   │   ├── export.py           GET /api/meetings/<id>/export
│   │   ├── health.py           GET /api/health
│   │   ├── meetings.py         /api/meetings CRUD
│   │   └── ui.py               GET / (serves index.html)
│   ├── static/
│   │   ├── app.js
│   │   └── styles.css
│   └── templates/
│       └── index.html
├── wsgi.py                     Gunicorn entry point
├── worker.py                   Standalone scheduler process entry point
└── requirements.txt

/etc/cklabScheduler/            root:cklabscheduler 750
└── cklabScheduler.env          root:cklabscheduler 640  — runtime configuration

/var/lib/cklabScheduler/        cklabscheduler:cklabscheduler 750
└── scheduler.db                SQLite database (WAL mode)

/etc/systemd/system/
├── cklab-scheduler-web.service
└── cklab-scheduler-worker.service

/etc/apache2/sites-available/
└── cklabscheduler.conf
```

---

## Configuration

All runtime configuration lives in `/etc/cklabScheduler/cklabScheduler.env`. During installation, `deploy/install.sh` prompts for each value and writes the file. See `.env.example` for documentation of every supported variable.

| Variable | Description |
|---|---|
| `REG_STATUS_HOST` | Pexip Management Node hostname (registration status API) |
| `COMMAND_HOST` | Pexip Conferencing Node hostname (dial/disconnect command API) |
| `MGMT_USER` | Pexip Management API username |
| `MGMT_PASS` | Pexip Management API password |
| `VERIFY_TLS` | `true`/`false` — verify TLS certificates on Pexip API calls |
| `APP_DISPLAY_NAME` | UI title and sidebar heading (default: `CKlabs Scheduler`) |
| `HOST_PIN` | PIN sent as host when dialling endpoints (leave blank for no PIN) |
| `CONTROL_DISPLAY_NAME` | Display name of the scheduler's Pexip control participant |
| `DIAL_PROTOCOL` | `sip`, `h323`, or `auto` |
| `ABOUT_TO_START_MINUTES` | Minutes before start to show yellow "about to start" status |
| `DEFAULT_EXTEND_MINUTES` | Default extension duration in minutes |
| `POLL_SECONDS` | Frontend polling interval in seconds |
| `SECRET_KEY` | Flask session secret (generated by installer, never prompted) |
| `DB_PATH` | SQLite database path |
| `O365_ENABLED` | `true`/`false` — enable Microsoft 365 email integration |
| `O365_TENANT_ID` | Azure AD tenant ID |
| `O365_CLIENT_ID` | Azure AD application (client) ID |
| `O365_CLIENT_SECRET` | Azure AD client secret |
| `O365_FROM_MAILBOX` | Mailbox to send invitations from |
| `O365_EMAIL_SUBJECT` | Invitation subject line |
| `O365_ORGANIZER_NAME` | Organizer display name in invitation |
| `O365_TIMEZONE` | IANA timezone for calendar invitations |
| `O365_INCLUDE_ICS` | `true`/`false` — attach `.ics` file to invitation |
| `O365_ALLOW_PROPOSE_NEW_TIME` | `true`/`false` |
| `O365_SAVE_TO_SENT_ITEMS` | `true`/`false` |
| `WEBRTC_BASE_URL` | WebRTC join link base URL (constructed from COMMAND_HOST if blank) |

---

## Fresh installation

Requires root on Ubuntu 24.04. The installer is interactive.

```bash
# 1. Copy the release archive to the server and extract
mkdir -p /root/cklabScheduler-src
tar -xzf cklabScheduler-test-r4.tar.gz -C /root/cklabScheduler-src --strip-components=1

# 2. Run the installer
cd /root/cklabScheduler-src
bash deploy/install.sh

# 3. Verify
bash deploy/verify_install.sh <server-hostname>
```

The installer:
- Installs system packages (`python3.12`, `apache2`, etc.)
- Creates the `cklabscheduler` service account
- Copies application files to `/opt/cklabScheduler/`
- Creates a Python virtual environment and installs dependencies
- Prompts for all required configuration values (passwords are not echoed)
- Generates `SECRET_KEY` automatically using `openssl rand -hex 32`
- Writes `/etc/cklabScheduler/cklabScheduler.env` (mode 640)
- Runs database schema migrations
- Configures and enables the Apache virtual host
- Installs and starts both systemd services
- Runs a health check

---

## Upgrade

From an existing installation:

```bash
# Extract new source alongside existing install
mkdir -p /root/cklabScheduler-new
tar -xzf cklabScheduler-test-r4.tar.gz -C /root/cklabScheduler-new --strip-components=1
cd /root/cklabScheduler-new
bash deploy/upgrade.sh
```

The upgrade script:
- Stops both services
- Backs up the database
- Replaces application files (preserves config and database)
- Updates Python dependencies
- Runs database migrations
- Updates systemd unit files
- Migrates Apache configuration if needed (r2 → r3 ProxyPass fix)
- Adds new env-file defaults without overwriting existing admin values
- Restarts both services
- Runs a health check

---

## Uninstall

```bash
bash deploy/uninstall.sh
```

Interactive prompts let you choose which components to remove. Configuration and database are preserved by default.

---

## Verification

After installation or upgrade:

```bash
bash deploy/verify_install.sh <server-hostname>
```

Checks: service account, directory permissions, env-file required keys, Python packages, both services active, port 5080 bound, Apache configuration, health endpoint.

Manual health check:

```bash
curl -sk "https://<server>/cklabScheduler/api/health" | python3 -m json.tool
```

---

## Running automated tests

Tests live in the repository under `tests/` and are **not** deployed to the server. Run them from the extracted source directory using the production virtual environment:

```bash
# On the Ubuntu server after installation
cd /root/cklabScheduler-src
/opt/cklabScheduler/venv/bin/python -m pytest tests/ -v
```

For local development (macOS/Linux), create a virtual environment first — see `CONTRIBUTING.md`.

---

## Troubleshooting

**Services**

```bash
systemctl status cklab-scheduler-web cklab-scheduler-worker
journalctl -u cklab-scheduler-web    -f
journalctl -u cklab-scheduler-worker -f
```

**Health check failing**

- Worker never started: check `journalctl -u cklab-scheduler-worker`
- Stale heartbeat: the worker crashed; restart it and check logs
- Database inaccessible: check ownership of `/var/lib/cklabScheduler/scheduler.db`

**Endpoint list empty**

- Verify `REG_STATUS_HOST` and `MGMT_USER`/`MGMT_PASS` in the env file
- Test credentials manually: `curl -sk -u <user>:<pass> https://<REG_STATUS_HOST>/api/admin/status/v1/registration_alias/?limit=10`
- Check `VERIFY_TLS` — set to `false` if using a self-signed Pexip certificate

**Meetings not starting**

- Confirm the meeting alias matches `^doc[a-zA-Z0-9]{16}$` (19 characters total)
- Confirm the alias is routable in Pexip (local policy, service config, or provisioned VMR)
- Check worker logs for dial errors

**Apache 500 on all requests**

- Confirm ProxyPass target includes the path prefix: `http://127.0.0.1:5080/cklabScheduler/`
- Test Gunicorn directly: `curl -s http://127.0.0.1:5080/cklabScheduler/api/health`
- Check `SCRIPT_NAME=/cklabScheduler` is set in the web service unit file

---

## Security considerations

See `SECURITY.md` for a full security overview.

---

## License

This software is proprietary and confidential. See `LICENSE` for details.
