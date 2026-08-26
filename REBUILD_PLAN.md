# cklabScheduler Rebuild Plan

## Overview

This document describes the target architecture for the production rebuild of cklabScheduler on Ubuntu 24.04. It serves as the authoritative reference before any implementation begins. The goal is a clean, installable application that preserves all existing functionality while correcting the bugs and architectural problems identified in the analysis.

No application redesign. No new features. Fix what is broken. Structure what is unstructured.

---

## 1. Target Directory Layout (on server)

```
/opt/cklabScheduler/                  ← application code (root:cklabscheduler 750)
├── app/
│   ├── __init__.py                   ← Flask factory (create_app), no APScheduler
│   ├── config.py                     ← Settings class + per-process startup validation
│   ├── database.py                   ← init_db(), db() helper, WAL + FK + busy_timeout pragmas
│   ├── pexip.py                      ← PexipAPI class only
│   ├── email_service.py              ← Graph token, send_o365_email, ICS, send_invites_for_meeting
│   ├── meeting_utils.py              ← classify_meeting, fetch_meeting_with_endpoints,
│   │                                    meetings_for_day, normalize_alias, build_webrtc_join_url,
│   │                                    validate_or_make_alias, normalize_live_participants,
│   │                                    endpoint_matches_live, safe_email, date/time helpers
│   ├── scheduler_jobs.py             ← start_due_meetings(), end_due_meetings(),
│   │                                    expire_missed_meetings(), recover_stuck_meetings(),
│   │                                    scheduler_tick()
│   │                                    Imported ONLY by worker.py — never by the web app
│   └── routes/
│       ├── __init__.py
│       ├── ui.py                     ← GET /
│       ├── meetings.py               ← /api/meetings CRUD routes
│       ├── endpoints.py              ← GET /api/endpoints, GET /api/config
│       ├── export.py                 ← GET /api/meetings/<id>/export, GET /api/export/meetings
│       └── health.py                 ← GET /api/health
├── templates/
│   └── index.html                    ← unchanged from current
├── static/
│   ├── app.js                        ← unchanged from current
│   └── styles.css                    ← unchanged from current
├── wsgi.py                           ← Gunicorn entry: from app import create_app; application = create_app()
├── worker.py                         ← Standalone scheduler process
├── requirements.txt                  ← Updated: adds python-dotenv
└── .env.example                      ← All supported variables documented

/etc/cklabScheduler/                  ← root:cklabscheduler 750
└── cklabScheduler.env                ← root:cklabscheduler 640

/var/lib/cklabScheduler/             ← cklabscheduler:cklabscheduler 750
└── scheduler.db                      ← SQLite database

/etc/systemd/system/
├── cklab-scheduler-web.service
└── cklab-scheduler-worker.service

/etc/apache2/sites-available/
└── cklabscheduler.conf
```

---

## 2. Service Account

```
username:  cklabscheduler
type:      system account (--system)
home:      none (--no-create-home)
shell:     /usr/sbin/nologin
groups:    cklabscheduler (primary only)
```

Both systemd services run as this account. The account has read access to `/opt/cklabScheduler` and `/etc/cklabScheduler/cklabScheduler.env`, and read/write access to `/var/lib/cklabScheduler`.

---

## 3. Systemd Services

### `cklab-scheduler-web.service`

Runs Gunicorn with the Flask application. **APScheduler is never started in this process.**

```ini
[Unit]
Description=CKlabs Scheduler Web Application
After=network.target

[Service]
Type=simple
User=cklabscheduler
Group=cklabscheduler
WorkingDirectory=/opt/cklabScheduler
EnvironmentFile=/etc/cklabScheduler/cklabScheduler.env
ExecStart=/opt/cklabScheduler/venv/bin/gunicorn \
    --bind 127.0.0.1:5080 \
    --workers 2 \
    --threads 4 \
    --worker-class gthread \
    --env SCRIPT_NAME=/cklabScheduler \
    --access-logfile - \
    --error-logfile - \
    wsgi:application
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cklab-scheduler-web

[Install]
WantedBy=multi-user.target
```

Multiple Gunicorn workers are safe because the web service and its workers never start or touch APScheduler.

### `cklab-scheduler-worker.service`

Runs the standalone APScheduler process. This is the **only** process that executes scheduler logic, starts meetings, or disconnects endpoints.

```ini
[Unit]
Description=CKlabs Scheduler Worker (meeting start/end automation)
After=network.target

[Service]
Type=simple
User=cklabscheduler
Group=cklabscheduler
WorkingDirectory=/opt/cklabScheduler
EnvironmentFile=/etc/cklabScheduler/cklabScheduler.env
ExecStart=/opt/cklabScheduler/venv/bin/python worker.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=cklab-scheduler-worker

[Install]
WantedBy=multi-user.target
```

`worker.py` structure:
- Calls `Settings.validate_worker()` — exits with a clear error if required worker config is missing
- Calls `init_db()` to ensure the database and schema are ready
- Creates `BackgroundScheduler(timezone="UTC")`
- Adds `scheduler_tick` with `"interval", seconds=10, max_instances=1` — `max_instances=1` prevents overlapping ticks if a tick runs long
- Records `worker_start_time` in process memory for heartbeat use
- Registers `SIGTERM` and `SIGINT` handlers that call `scheduler.shutdown(wait=True)` cleanly before exiting
- Blocks until signaled

---

## 4. Apache Configuration

Apache terminates HTTPS and reverse-proxies to Gunicorn. No mod_wsgi.

Required Apache modules: `proxy`, `proxy_http`, `ssl`, `headers`.

### Path and SCRIPT_NAME Behavior

The ProxyPass configuration uses **trailing slashes on both sides**. This is the only form with well-defined behavior in the Apache documentation. The consequence is that the `/cklabScheduler` prefix is stripped by Apache before Gunicorn receives the request; Gunicorn's `--env SCRIPT_NAME=/cklabScheduler` then tells Werkzeug what the original mount path was.

**End-to-end path handling for each URL pattern:**

| Browser request | Rule that fires | What Gunicorn receives | PATH_INFO / SCRIPT_NAME in Flask |
|---|---|---|---|
| `GET /cklabScheduler` | `RedirectMatch` | *(browser redirected)* | *(n/a)* |
| `GET /cklabScheduler/` | `ProxyPass` | `GET /` | PATH_INFO=`/`, SCRIPT_NAME=`/cklabScheduler` |
| `GET /cklabScheduler/api/meetings` | `ProxyPass` | `GET /api/meetings` | PATH_INFO=`/api/meetings`, SCRIPT_NAME=`/cklabScheduler` |
| `GET /cklabScheduler/static/app.js` | `ProxyPass` | `GET /static/app.js` | PATH_INFO=`/static/app.js`, SCRIPT_NAME=`/cklabScheduler` |

Flask routes are registered against `PATH_INFO` (e.g., `@app.route("/api/meetings")`). SCRIPT_NAME is used by Werkzeug for URL generation:

- `request.script_root` → `"/cklabScheduler"` → Jinja2 injects `window.APP_ROOT = "/cklabScheduler"` → frontend uses `API_BASE = "/cklabScheduler/api"` ✓
- `url_for('static', filename='app.js')` → `"/cklabScheduler/static/app.js"` ✓
- Flask-generated redirect `Location:` headers include SCRIPT_NAME → `ProxyPassReverse` rewrites any `http://127.0.0.1:5080/...` locations back to the external path ✓

The bare `/cklabScheduler` path does not match `ProxyPass /cklabScheduler/ ...` (note the trailing slash requirement). A `RedirectMatch` handles it with an exact-match regex (`^` and `$` anchors) so that only the bare path is redirected, not `/cklabScheduler/api/...`.

```apache
# /etc/apache2/sites-available/cklabscheduler.conf

# HTTP → HTTPS redirect
<VirtualHost *:80>
    ServerName YOUR_SERVER_HOSTNAME
    Redirect permanent / https://YOUR_SERVER_HOSTNAME/
</VirtualHost>

<VirtualHost *:443>
    ServerName YOUR_SERVER_HOSTNAME

    SSLEngine on
    SSLCertificateFile    /etc/ssl/certs/cklabscheduler.crt
    SSLCertificateKeyFile /etc/ssl/private/cklabscheduler.key

    # Exact redirect: /cklabScheduler (no trailing slash) -> /cklabScheduler/
    # Uses anchored regex to avoid matching /cklabScheduler/anything
    RedirectMatch permanent ^/cklabScheduler$ /cklabScheduler/

    # Reverse proxy to Gunicorn. Trailing slashes on BOTH sides are required.
    # Apache strips the /cklabScheduler/ prefix before forwarding.
    # Gunicorn --env SCRIPT_NAME=/cklabScheduler restores it for Flask URL generation.
    ProxyPreserveHost On
    ProxyPass        /cklabScheduler/ http://127.0.0.1:5080/
    ProxyPassReverse /cklabScheduler/ http://127.0.0.1:5080/

    RequestHeader set X-Forwarded-Proto "https"

    ErrorLog  ${APACHE_LOG_DIR}/cklabscheduler_error.log
    CustomLog ${APACHE_LOG_DIR}/cklabscheduler_access.log combined
</VirtualHost>
```

**TLS certificates:** The install script offers two options:
1. Provide paths to an existing certificate and key
2. Generate a self-signed certificate (for lab/internal use only)

Let's Encrypt/certbot integration is out of scope and left as an optional post-install step.

---

## 5. Database

- **Engine:** SQLite
- **Production path:** `/var/lib/cklabScheduler/scheduler.db`
- **Schema:** four tables — `meetings`, `meeting_endpoints`, `meeting_invitees`, `scheduler_heartbeat`

### Per-connection PRAGMAs (set in `db()` helper)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

- **WAL mode:** set once in `init_db()` and remembered by SQLite; ensures readers don't block the scheduler writer and the scheduler writer doesn't block readers
- **foreign_keys:** enforced per-connection since SQLite requires this each time
- **busy_timeout 5000ms:** prevents `sqlite3.OperationalError: database is locked` under concurrent web worker writes; waits up to 5 seconds for a write lock before failing

### Tables

#### `meetings` (existing, extended)

| Column | Change |
|---|---|
| `status` | Adds two new internal transition values: `starting`, `ending` |

All other columns and all other tables (`meeting_endpoints`, `meeting_invitees`) are unchanged from the current schema.

New valid values for `meetings.status`:

| Value | Meaning |
|---|---|
| `scheduled` | No action taken; waiting for start time |
| `starting` | Worker has claimed this meeting and is executing Pexip start operations |
| `started` | Running normally; all dials succeeded |
| `started_with_errors` | Running; at least one dial failed |
| `ending` | Worker has claimed this meeting and is executing Pexip disconnect |
| `ended` | Fully ended |
| `ended_with_errors` | Disconnect attempted but Pexip reported an error |

The `started_at` and `ended_at` timestamp columns remain the source of truth for user-facing timeline status (see §6 `classify_meeting`).

#### `scheduler_heartbeat` (new)

```sql
CREATE TABLE IF NOT EXISTS scheduler_heartbeat (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    last_seen   TEXT    NOT NULL,
    worker_pid  INTEGER NOT NULL,
    worker_start TEXT   NOT NULL
);
```

The `CHECK (id = 1)` constraint enforces a single-row table — no unbounded growth. Updated with `INSERT OR REPLACE` on every scheduler tick.

### `init_db()` is idempotent

Uses `CREATE TABLE IF NOT EXISTS` throughout. Safe to call on an existing database to apply WAL mode and add new tables introduced in future versions.

---

## 6. Application Code Structure

### `app/config.py` — Settings and Validation

The `Settings` class reads from environment variables at class definition time. **No sensitive value has a code default.** Sensitive variables that are absent cause the process to log a clear summary of all missing variables and exit.

Validation is split by process so each service only enforces the configuration it actually needs.

#### `Settings.validate_web()` — called by `create_app()`

Required:
- `REG_STATUS_HOST`
- `COMMAND_HOST`
- `MGMT_USER`
- `MGMT_PASS`
- `SECRET_KEY`

#### `Settings.validate_worker()` — called by `worker.py`

Required:
- `COMMAND_HOST`

`REG_STATUS_HOST`, `MGMT_USER`, and `MGMT_PASS` are **not** required by the worker. The scheduler worker only ever calls the Pexip Client API (`/api/client/v2/…`) on `COMMAND_HOST` using token-based authentication — it never calls the Management Status API (`/api/admin/status/v1/…`) on `REG_STATUS_HOST`. The Management Status API (and therefore `MGMT_USER`/`MGMT_PASS` basic auth) is used exclusively by the `/api/endpoints` web route.

`SECRET_KEY` is also not required; the worker handles no HTTP sessions.

O365 credentials are not required by the worker. Email sending occurs exclusively in the web process (at meeting create/update time). The scheduler worker never sends email.

#### Conditional validation (web process only, when `O365_ENABLED=true`)

If `O365_ENABLED=true`, `validate_web()` additionally requires:
- `O365_TENANT_ID`
- `O365_CLIENT_ID`
- `O365_CLIENT_SECRET`
- `O365_FROM_MAILBOX`

Validation collects all missing variables first, then raises a single `RuntimeError` listing them all, rather than stopping at the first missing one.

#### Variables with non-sensitive defaults (safe to ship in source code)

| Variable | Default | Notes |
|---|---|---|
| `DB_PATH` | `/var/lib/cklabScheduler/scheduler.db` | Production path; override in dev only |
| `VERIFY_TLS` | `true` | **Changed from original `false`** |
| `REG_VERIFY_TLS` | inherits `VERIFY_TLS` | |
| `COMMAND_VERIFY_TLS` | inherits `VERIFY_TLS` | |
| `HOST_PIN` | `""` | Empty = PIN-less conference; **"2024" default removed** |
| `CONTROL_DISPLAY_NAME` | `"Scheduler"` | |
| `DIAL_PROTOCOL` | `"auto"` | |
| `ABOUT_TO_START_MINUTES` | `1` | |
| `DEFAULT_EXTEND_MINUTES` | `15` | |
| `POLL_SECONDS` | `3` | Returned to frontend only |
| `WEBRTC_BASE_URL` | constructed from `COMMAND_HOST` | |
| `O365_ENABLED` | `false` | |
| `O365_SAVE_TO_SENT_ITEMS` | `true` | |
| `O365_EMAIL_SUBJECT` | `"Your Secure Virtual Consultation"` | |
| `O365_INCLUDE_ICS` | `true` | |
| `O365_TIMEZONE` | `"America/New_York"` | |
| `O365_ORGANIZER_NAME` | `"Pexip Scheduler"` | |
| `O365_LOCATION` | `"Secure Virtual Session"` | |
| `O365_ALLOW_PROPOSE_NEW_TIME` | `false` | |

#### Variables that are required and have no default (must be in env file)

- `REG_STATUS_HOST`
- `COMMAND_HOST`
- `MGMT_USER`
- `MGMT_PASS`
- `SECRET_KEY` (web process only; generated by install script)

### `app/scheduler_jobs.py` — Hard Architectural Boundary

Contains `start_due_meetings()`, `end_due_meetings()`, `expire_missed_meetings()`, `recover_stuck_meetings()`, and `scheduler_tick()`. **Not imported anywhere in the web application.** Only `worker.py` imports this module. If any web route module ever imports from `scheduler_jobs`, that is a bug.

### Route Blueprints

All existing routes are preserved with identical URLs and behavior.

| Blueprint | File | Routes |
|---|---|---|
| UI | routes/ui.py | `GET /` |
| Meetings | routes/meetings.py | `GET /api/meetings`, `POST /api/meetings`, `POST /api/meetings/<id>/extend`, `POST /api/meetings/<id>/delete`, `POST /api/meetings/<id>/update`, `POST /api/meetings/<id>/redial_endpoint`, `POST /api/meetings/<id>/invitees/<id>/resend` |
| Endpoints | routes/endpoints.py | `GET /api/endpoints`, `GET /api/config` |
| Export | routes/export.py | `GET /api/meetings/<id>/export`, `GET /api/export/meetings` |
| Health | routes/health.py | `GET /api/health` |

### `classify_meeting()` — Updated for Transition States

The `classify_meeting()` helper determines user-visible `timeline_status`. It is updated to handle the two new internal states without changing any user-facing status values:

```
status='starting' (started_at IS NULL) → timeline_status='started'
    (the meeting is actively being started; showing 'started' is more accurate than 'scheduled')

status='ending' (started_at IS SET, ended_at IS NULL) → timeline_status='started'
    (unchanged — started_at being set already causes this result)

status='ended' or 'ended_with_errors' → timeline_status='ended'
    (unchanged)
```

The existing `started`, `scheduled`, `about_to_start` transitions are unchanged.

---

## 7. Scheduler State Machine

### State Diagram

```
  scheduled ──(start_time reached, per-meeting atomic claim)──→ starting
                 │
                 ├──(all dials OK)──────────────────────────────→ started
                 ├──(some dials fail)────────────────────────────→ started_with_errors
                 ├──(end_time also passed before recovery)────────→ ended_with_errors
                 └──(stuck > CRASH_RECOVERY_MINUTES, window open)─→ [recovery: see below]

  scheduled ──(end_time already passed, window missed entirely)──→ ended_with_errors
               (expire_missed_meetings; no Pexip calls made)

  started / started_with_errors
               ──(end_time reached, per-meeting atomic claim)──→ ending
                    │
                    ├──(disconnect OK)──────────────────────────→ ended
                    ├──(disconnect fails)───────────────────────→ ended_with_errors
                    └──(stuck > CRASH_RECOVERY_MINUTES)─────────→ [recovery: re-attempt disconnect]
```

`CRASH_RECOVERY_MINUTES = 2`. The scheduler tick fires every 10 seconds; 2 minutes means 12 consecutive missed ticks before a transition is considered stuck.

### Atomic Claim Queries (per-meeting)

Each eligible meeting is claimed **individually** with a WHERE clause that includes the meeting ID. This allows the rowcount to be verified before any Pexip operation is issued for that specific meeting. A bulk UPDATE that claims all eligible meetings in one statement would not provide this guarantee.

**Start claim sequence:**
```
1. SELECT id, meeting_alias FROM meetings
   WHERE status = 'scheduled' AND start_time <= ? AND end_time > ?

2. For each row returned:
   a. UPDATE meetings SET status = 'starting', updated_at = ?
      WHERE id = ? AND status = 'scheduled'
   b. COMMIT this update immediately (the claim is durable before any Pexip call)
   c. If cursor.rowcount != 1: skip this meeting and continue to the next
      (Defensive: should not occur with max_instances=1 and a single worker process,
       but protects against crash-restart edge cases)
   d. Proceed with Pexip start operations for this meeting only
```

**End claim sequence:**
```
1. SELECT id, meeting_alias FROM meetings
   WHERE status IN ('started', 'started_with_errors')
     AND end_time <= ?
     AND ended_at IS NULL

2. For each row returned:
   a. UPDATE meetings SET status = 'ending', updated_at = ?
      WHERE id = ? AND status IN ('started', 'started_with_errors') AND ended_at IS NULL
   b. COMMIT immediately
   c. If cursor.rowcount != 1: skip
   d. Proceed with Pexip disconnect for this meeting only
```

### `scheduler_tick()` Execution Order

Each tick runs these four steps in order:

1. **`recover_stuck_meetings()`** — resolves any meetings left in `starting` or `ending` from a prior crash
2. **`expire_missed_meetings()`** — terminates any `scheduled` meetings whose entire window has passed without the worker being available
3. **`end_due_meetings()`** — claims and disconnects meetings whose `end_time` has passed
4. **`start_due_meetings()`** — claims and starts meetings whose `start_time` has passed

Running expire and end before start ensures that a meeting whose entire window elapsed during an outage is marked terminal before the next start cycle runs, and that a meeting whose start and end both pass between ticks is expired rather than started.

### `expire_missed_meetings()`

Finds meetings where the entire scheduled window elapsed while the worker was unavailable. These meetings were never started and will never be started; their endpoints are never dialed.

```
SELECT id, meeting_alias FROM meetings
WHERE status = 'scheduled' AND end_time <= ?

For each:
  UPDATE meetings
  SET status = 'ended_with_errors', ended_at = ?, updated_at = ?
  WHERE id = ? AND status = 'scheduled'

  If cursor.rowcount == 1:
    Log WARNING: "Meeting {alias} (id={id}): window elapsed before scheduler automation
                  could start it. Marked ended_with_errors. started_at remains NULL."
```

`started_at` is left NULL, recording that the meeting was never actually started. `ended_at` is set to the current time of discovery. The meeting's `timeline_status` will be `ended` (since `status='ended_with_errors'` maps to `ended` in `classify_meeting`), which is the correct user-visible state for a past meeting.

### `recover_stuck_meetings()`

Handles meetings left in `starting` or `ending` by a previous worker crash.

**For each meeting WHERE `status = 'starting'` AND `updated_at < now - CRASH_RECOVERY_MINUTES`:**

```
If end_time <= now (window has passed since the crash):
  UPDATE meetings SET status='ended_with_errors', ended_at=now WHERE id=? AND status='starting'
  Log WARNING: "Meeting {alias}: window passed during crash recovery; marking ended_with_errors"
  (No Pexip operations. Endpoints remain at whatever status they had before the crash.)

Else (window still open — re-attempt start with live participant check):
  1. Acquire token: request_control_token(meeting_alias)
     → On failure: mark meeting started_with_errors, log error, skip

  2. Fetch live participants:
     GET /api/client/v2/conferences/{alias}/participants using token
     → On failure: release token, mark started_with_errors, log, skip
     → Normalize the participant list (normalize_live_participants)

  3. For each endpoint in meeting_endpoints WHERE meeting_id=? AND status != 'dialed':

     a. Check: endpoint_matches_live(endpoint_alias, display_name, normalized_live)

        IF MATCHED (endpoint already connected to the conference):
          UPDATE meeting_endpoints SET status='dialed' WHERE id=?
          COMMIT immediately
          Log INFO: "Recovery: {endpoint_alias} already connected; marked dialed, no redial issued"

        IF NOT MATCHED (endpoint absent from conference):
          issue_dial = dial_endpoint_to_meeting(meeting_alias, endpoint_alias, token, role)
          UPDATE meeting_endpoints SET status='dialed', dial_response=? WHERE id=?
          COMMIT immediately
          Log INFO: "Recovery: {endpoint_alias} not in conference; redialed"

          On dial failure:
          UPDATE meeting_endpoints SET status='error', dial_response=? WHERE id=?
          COMMIT immediately
          Log WARNING: "Recovery: {endpoint_alias} redial failed: {error}"

  4. release_control_token(meeting_alias, token)

  5. Determine meeting outcome: all_ok = no endpoint in 'error' status
     UPDATE meetings SET status=('started' if all_ok else 'started_with_errors'),
                         started_at=now, updated_at=now
     WHERE id=? AND status='starting'
     COMMIT
```

**Unavoidable race condition (documented):**
If a dial was accepted by Pexip in the instant before the worker crashed, the participant may not yet be visible in the conference when the live participant list is fetched during recovery (participant appearance typically lags dial acceptance by under one second). In this window, the endpoint will not match any live participant and a second dial will be issued. This may create a duplicate connection. This race cannot be eliminated without Pexip providing a server-side idempotent dial operation. The window is very small; operators may observe a participant appearing twice in the conference in this scenario.

**For each meeting WHERE `status = 'ending'` AND `updated_at < now - CRASH_RECOVERY_MINUTES`:**

```
Re-attempt disconnect: request_control_token(alias) → disconnect_conference(alias, token) → release
  → On success: UPDATE meetings SET status='ended', ended_at=now WHERE id=? AND status='ending'
  → On failure (including conference already gone): 
       UPDATE meetings SET status='ended_with_errors', ended_at=now WHERE id=? AND status='ending'
       Log WARNING: "Recovery: disconnect failed for {alias}: {error}"
COMMIT
```

Disconnect is treated as effectively idempotent from the recovery perspective: if the conference is already gone (the first disconnect succeeded before the crash, or the conference ended on its own), the Pexip API will return an error, which recovery catches, logs, and resolves by marking `ended_with_errors`. This is acceptable — the meeting is over.

### Endpoint Status Commit Policy

Both during normal start (`start_due_meetings`) and during crash recovery, each endpoint's status is committed to the database **immediately after each individual dial result** rather than batched until all endpoints are processed. This minimizes the crash window: if the worker crashes mid-start, the database accurately reflects which endpoints were successfully dialed, reducing unnecessary re-dials during recovery.

### `meeting_endpoints` During Transitions

| Scenario | Endpoint status at that point |
|---|---|
| After claim (status=`starting`) | `scheduled` (unchanged) |
| After successful `dial` call | `dialed` (committed immediately) |
| After failed `dial` call | `error` (committed immediately) |
| After recovery finds endpoint already live | `dialed` (no redial; committed immediately) |
| After `ending` completes (either result) | `ended` (all endpoints for that meeting) |

---

## 8. Scheduler Worker Heartbeat

### Purpose

`/api/health` needs to report whether the scheduler worker is running and healthy **without requiring systemd privileges** from the web process. The web process reads the heartbeat from SQLite instead of querying systemd.

### Heartbeat Write (worker process)

The worker updates `scheduler_heartbeat` at the start of every `scheduler_tick()`, before any business logic:

```python
conn.execute(
    "INSERT OR REPLACE INTO scheduler_heartbeat "
    "(id, last_seen, worker_pid, worker_start) VALUES (1, ?, ?, ?)",
    (iso(now_utc()), os.getpid(), worker_start_iso),
)
conn.commit()
```

`worker_start_iso` is captured once at process start and held in memory, making it possible to detect restarts (PID or start time changes) from the health endpoint if needed in the future.

### Heartbeat Read (web process, health endpoint)

```python
row = conn.execute(
    "SELECT last_seen, worker_pid FROM scheduler_heartbeat WHERE id = 1"
).fetchone()
```

- If no row: worker has never run since the database was created → `{"ok": false, "status": "never_started"}`
- If row exists: compute `age_seconds = (now_utc() - parse_iso(row["last_seen"])).total_seconds()`
  - `age_seconds < 30`: healthy (within 3× the 10-second tick interval)
  - `age_seconds >= 30`: unhealthy → `{"ok": false, "status": "stale", "last_heartbeat_seconds_ago": <int>}`
  - On healthy: `{"ok": true, "last_heartbeat_seconds_ago": <int>}`

The 30-second threshold is 3× the tick interval. It tolerates two missed ticks before flagging unhealthy.

---

## 9. `GET /api/health` Specification

Reports application health **without exposing any infrastructure details** — no hostnames, no URLs, no credentials, no PINs, no tenant IDs, no IP addresses.

### Response schema

```json
{
  "ok": true,
  "service": "cklabScheduler",
  "version": "2.0.0",
  "database": {
    "ok": true,
    "meeting_count": 12
  },
  "pexip": {
    "configured": true
  },
  "o365": {
    "enabled": false,
    "configured": false
  },
  "scheduler_worker": {
    "ok": true,
    "last_heartbeat_seconds_ago": 8
  }
}
```

### Field definitions

| Field | What it reports | What it does NOT report |
|---|---|---|
| `database.ok` | Can the DB be opened and queried | DB path, file size |
| `database.meeting_count` | `SELECT COUNT(*) FROM meetings` | Any meeting details |
| `pexip.configured` | `REG_STATUS_HOST`, `COMMAND_HOST`, `MGMT_USER`, `MGMT_PASS` are all non-empty | Hostnames, usernames, passwords |
| `o365.enabled` | Value of `O365_ENABLED` | — |
| `o365.configured` | All four O365 vars non-empty (only meaningful when enabled) | Tenant ID, client ID, secret, mailbox |
| `scheduler_worker.ok` | Heartbeat age < 30s | PID, start time |
| `scheduler_worker.last_heartbeat_seconds_ago` | Age of last heartbeat in seconds | — |
| `scheduler_worker.status` | `"never_started"` or `"stale"` on failure only | — |

**No live Pexip connectivity check.** The health endpoint must return quickly and must not trigger outbound network calls. A reachability check would add latency to every health poll and could time out if Pexip is unreachable.

### HTTP status codes

- `200`: all components ok
- `500`: database error or any component `ok=false`

The overall `"ok"` field at the root is `true` only when all component checks pass.

---

## 10. Configuration File

`/etc/cklabScheduler/cklabScheduler.env` — `KEY=VALUE` format, one per line. Loaded by systemd `EnvironmentFile=`. Both services use the same file.

Permissions: `root:cklabscheduler 640` — service account can read, no other unprivileged user can.

The `.env.example` in the repository documents every supported variable with a descriptive comment, including all O365 variables currently missing from the example.

---

## 11. Data Flow

### HTTP request (browser → Flask)

```
Browser
  → HTTPS :443 Apache
    → RedirectMatch: /cklabScheduler → /cklabScheduler/ (301, browser follows)
    → ProxyPass strips /cklabScheduler/ prefix
    → HTTP :5080 Gunicorn (SCRIPT_NAME=/cklabScheduler in WSGI environ via --env)
      → Flask: PATH_INFO=/<rest>, SCRIPT_NAME=/cklabScheduler
        → Route handler
          ↕ SQLite /var/lib/cklabScheduler/scheduler.db (read)
          ↕ Pexip APIs (for /api/endpoints, live participant fetch)
          ↕ Microsoft Graph API (for email send)
      ← JSON / HTML / CSV response
    ← ProxyPassReverse rewrites any Location: headers
  ← HTTPS response
```

### Scheduler tick (worker process)

```
systemd → worker.py
  → APScheduler BackgroundScheduler (max_instances=1)
    → scheduler_tick() every 10 seconds
      → SQLite: write heartbeat
      → recover_stuck_meetings(): resolve meetings stuck in starting/ending > 2 min
      → expire_missed_meetings(): mark scheduled meetings whose window has entirely passed
      → end_due_meetings(): per-meeting atomic claim (status → 'ending'), Pexip disconnect
      → start_due_meetings(): per-meeting atomic claim (status → 'starting'), Pexip token+dial
```

### Shared database (both processes)

Web workers (2 Gunicorn workers × 4 threads = up to 8 threads) read the database on every `/api/meetings` poll. The scheduler worker writes on every tick. SQLite WAL mode allows all readers to proceed concurrently with one writer. The `busy_timeout=5000` pragma ensures write contention retries for up to 5 seconds rather than failing immediately.

---

## 12. Bugs Fixed in This Rebuild

| # | Bug | Fix |
|---|---|---|
| 1 | `python-dotenv` missing from `requirements.txt` | Added to `requirements.txt` |
| 2 | Hardcoded Pexip hostnames as `Settings` defaults | `REG_STATUS_HOST` and `COMMAND_HOST` now required; no default |
| 3 | `HOST_PIN` defaults to `"2024"` | Default changed to `""` (no PIN); not required |
| 4 | `VERIFY_TLS` defaults to `false` in code | Default changed to `true` |
| 5 | Dead code: `_command_request` never called | Removed |
| 6 | APScheduler starts in every process/worker | APScheduler exists only in `worker.py` |
| 7 | APScheduler never shut down on process exit | `SIGTERM`/`SIGINT` handlers call `scheduler.shutdown(wait=True)` in worker |
| 8 | `print()` used for all logging | Replaced with `logging` module; journals via systemd |
| 9 | WAL mode never enabled | `PRAGMA journal_mode=WAL` in `init_db()` |
| 10 | FK constraints declared but never enforced | `PRAGMA foreign_keys=ON` set per connection in `db()` |
| 11 | No `busy_timeout` — concurrent writes could raise `OperationalError` | `PRAGMA busy_timeout=5000` set per connection |
| 12 | `/api/health` missing despite README claiming it exists | Implemented per §9 |
| 13 | O365 settings absent from `.env.example` | All variables added with comments |
| 14 | `send_invites_for_meeting` resends to all invitees on update | On update, only invitees with `email_status='pending'` receive email; existing `sent` invitees are not re-emailed |
| 15 | No Flask `SECRET_KEY` configured | `SECRET_KEY` added to required config; generated by install script |
| 16 | DB stored next to `app.py` | Moved to `/var/lib/cklabScheduler/scheduler.db` |
| 17 | APScheduler scheduler in web process allows duplicate meeting starts/ends with multiple workers | Web process never starts APScheduler; worker is the sole executor |
| 18 | No crash recovery: meetings stuck mid-start or mid-end after worker restart | `recover_stuck_meetings()` + `starting`/`ending` transition states |
| 19 | No way for `/api/health` to know if scheduler worker is running | `scheduler_heartbeat` table; worker writes on every tick |
| 20 | Missing startup config validation | `validate_web()` and `validate_worker()` fail fast with all missing vars listed |
| 21 | Atomic claim SQL was a bulk UPDATE across all eligible meetings, not per-meeting | Each meeting is claimed individually with `WHERE id=? AND status=<expected>` and `cursor.rowcount` verified before any Pexip operation |
| 22 | Crash recovery assumed Pexip gracefully rejects duplicate dials | Recovery fetches live participants first; endpoints already in the conference are marked `dialed` without issuing another dial; only absent endpoints are redialed |
| 23 | `scheduled` meetings whose entire window elapsed while the worker was unavailable would remain `scheduled` forever | `expire_missed_meetings()` runs each tick before start processing; such meetings are transitioned to `ended_with_errors` with `ended_at` set and no Pexip calls made |
| 24 | Worker validation incorrectly required `REG_STATUS_HOST`, `MGMT_USER`, `MGMT_PASS` | Worker only requires `COMMAND_HOST`; the Management Status API and basic auth credentials are used exclusively by the web process's `/api/endpoints` route |

### Bugs documented but NOT fixed (preserved as-is per requirement to preserve existing functionality)

- `meetings_for_day` treats the date parameter as UTC midnight; non-UTC users see off-by-one day boundaries. Fix requires a UI/API change.
- Frontend polling interval is hardcoded to 3000ms and ignores `POLL_SECONDS` from `/api/config`. Fix requires a frontend change.
- `update_meeting` deletes and recreates all endpoint rows, losing dial history. Consistent with how the edit dialog is presented in the UI.
- ICS line-folding is by character, not by octet (RFC 5545 requires octet-count). Harmless in practice for ASCII content.
- Google Fonts loaded from `fonts.googleapis.com`. Preserved to avoid UI changes.

---

## 13. `deploy/install.sh` — Interactive Installer

Runs on a blank Ubuntu 24.04 server as root. Designed to be re-runnable (idempotent where safe).

### Flow

```
1.  Pre-flight checks
    ├── Verify running as root
    ├── Verify Ubuntu 24.04 (/etc/os-release check)
    └── Check internet connectivity

2.  System packages
    ├── apt-get update -qq
    └── apt-get install -y python3.12 python3.12-venv python3-tzdata apache2 openssl

3.  Apache modules
    └── a2enmod proxy proxy_http ssl headers

4.  Service account
    └── useradd --system --no-create-home --shell /usr/sbin/nologin cklabscheduler
        (skipped silently if already exists)

5.  Directory creation with ownership
    ├── /opt/cklabScheduler          root:cklabscheduler  750
    ├── /etc/cklabScheduler          root:cklabscheduler  750
    └── /var/lib/cklabScheduler      cklabscheduler:cklabscheduler  750

6.  Application files
    ├── Copy repository contents to /opt/cklabScheduler
    │   (excludes: .git, venv, __pycache__, *.db, .env*, *.pyc)
    └── Set ownership: root:cklabscheduler, dirs 750, files 640

7.  Python virtual environment
    ├── python3 -m venv /opt/cklabScheduler/venv
    └── /opt/cklabScheduler/venv/bin/pip install -r requirements.txt

8.  Database migration prompt (optional)
    ├── Prompt: "Do you have an existing scheduler.db to migrate? [y/N]"
    ├── If yes:
    │   ├── Prompt: "Path to existing scheduler.db:"
    │   ├── Validate: file exists, is readable, is a SQLite database
    │   │   (run: sqlite3 <path> "SELECT COUNT(*) FROM meetings" — if this succeeds, it's valid)
    │   ├── Copy to /var/lib/cklabScheduler/scheduler.db
    │   ├── chown cklabscheduler:cklabscheduler /var/lib/cklabScheduler/scheduler.db
    │   ├── Create backup: cp scheduler.db scheduler.db.pre-install-TIMESTAMP.bak
    │   └── Print: "Existing database copied and backed up."
    └── (Whether migrating or fresh, step 11 runs init_db() to add new schema.)

9.  Interactive configuration prompts
    ┌─ Pexip ──────────────────────────────────────────────────────────────┐
    │  REG_STATUS_HOST     (required)                   [echo on]          │
    │  COMMAND_HOST        (required)                   [echo on]          │
    │  MGMT_USER           (required)                   [echo on]          │
    │  MGMT_PASS           (required)                   [echo OFF]         │
    │  VERIFY_TLS          [true/false, default: true]                     │
    │  HOST_PIN            [optional]                   [echo OFF]         │
    │  CONTROL_DISPLAY_NAME[default: Scheduler]                            │
    │  DIAL_PROTOCOL       [default: auto]                                 │
    │  WEBRTC_BASE_URL     [optional; press Enter to construct from        │
    │                       COMMAND_HOST automatically]                    │
    ├─ Scheduler ──────────────────────────────────────────────────────────┤
    │  ABOUT_TO_START_MINUTES [default: 1]                                 │
    │  DEFAULT_EXTEND_MINUTES [default: 15]                                │
    │  POLL_SECONDS           [default: 3]                                 │
    ├─ Microsoft 365 ──────────────────────────────────────────────────────┤
    │  O365_ENABLED        [yes/no, default: no]                           │
    │  (the following are only prompted if O365_ENABLED=yes)               │
    │  O365_TENANT_ID      [echo OFF]                                      │
    │  O365_CLIENT_ID      [echo on]                                       │
    │  O365_CLIENT_SECRET  [echo OFF]                                      │
    │  O365_FROM_MAILBOX   [echo on]                                       │
    │  O365_EMAIL_SUBJECT  [default: Your Secure Virtual Consultation]     │
    │  O365_ORGANIZER_NAME [default: Pexip Scheduler]                      │
    │  O365_TIMEZONE       [default: America/New_York]                     │
    │  O365_LOCATION       [default: Secure Virtual Session]               │
    │  O365_INCLUDE_ICS    [yes/no, default: yes]                          │
    │  O365_ALLOW_PROPOSE_NEW_TIME [yes/no, default: no]                   │
    │  O365_SAVE_TO_SENT_ITEMS     [yes/no, default: yes]                  │
    ├─ Apache / TLS ───────────────────────────────────────────────────────┤
    │  Server hostname (ServerName for VirtualHost)   [echo on]            │
    │  TLS option:                                                         │
    │    [1] I have an existing certificate and key — provide file paths   │
    │    [2] Generate a self-signed certificate (lab/internal use only)    │
    └──────────────────────────────────────────────────────────────────────┘

    SECRET_KEY is generated automatically (openssl rand -hex 32).
    Not prompted; never echoed; written directly to the env file.

10. Write configuration file
    ├── Write /etc/cklabScheduler/cklabScheduler.env
    └── chown root:cklabscheduler; chmod 640

11. TLS certificate
    ├── Option 1: validate provided cert/key paths; write paths into Apache config
    └── Option 2: openssl req -x509 -newkey rsa:4096 -days 3650 -nodes ...
                  → /etc/ssl/certs/cklabscheduler.crt
                  → /etc/ssl/private/cklabscheduler.key

12. Database initialisation
    └── Run init_db() via:
        DB_PATH=/var/lib/cklabScheduler/scheduler.db \
        /opt/cklabScheduler/venv/bin/python -c \
          "from app.database import init_db; init_db()"
        (Idempotent: adds new tables and WAL pragma; safe on migrated databases)

13. Systemd unit files
    ├── Write /etc/systemd/system/cklab-scheduler-web.service
    ├── Write /etc/systemd/system/cklab-scheduler-worker.service
    └── systemctl daemon-reload

14. Apache virtual host
    ├── Write /etc/apache2/sites-available/cklabscheduler.conf
    ├── a2ensite cklabscheduler
    ├── apache2ctl configtest  (abort on error)
    └── systemctl reload apache2

15. Start services
    ├── systemctl enable cklab-scheduler-web cklab-scheduler-worker
    └── systemctl start  cklab-scheduler-web cklab-scheduler-worker

16. Health check
    ├── sleep 5  (allow Gunicorn to bind and worker to record first heartbeat)
    ├── curl -sk https://localhost/cklabScheduler/api/health
    ├── If ok=true:  print access URL, print journalctl commands, exit 0
    └── If ok=false: print health JSON, print troubleshooting hints, exit 1
```

---

## 14. `deploy/upgrade.sh`

Non-interactive. Runs as root. Assumes the application is already installed.

**Both services are stopped before any file or dependency changes.** This prevents the worker from processing meetings against partially-updated code or a mid-migration database.

```
1.  Verify running as root
2.  Verify install exists (/opt/cklabScheduler/venv and /etc/cklabScheduler/cklabScheduler.env)

3.  Stop BOTH services
    ├── systemctl stop cklab-scheduler-web
    └── systemctl stop cklab-scheduler-worker
        (Any meetings in 'starting' or 'ending' will be recovered on worker restart
         by recover_stuck_meetings() if the window has not passed.)

4.  Back up database
    └── cp /var/lib/cklabScheduler/scheduler.db
           /var/lib/cklabScheduler/scheduler.db.bak.$(date +%Y%m%dT%H%M%S)

5.  Replace application files
    └── Copy new source tree to /opt/cklabScheduler
        (excludes: venv, .env*, *.db, .git, __pycache__, *.pyc)
        Fix ownership: root:cklabscheduler, dirs 750, files 640

6.  Update Python dependencies
    └── /opt/cklabScheduler/venv/bin/pip install --upgrade -r requirements.txt

7.  Run database migrations (init_db is idempotent)
    └── DB_PATH=/var/lib/cklabScheduler/scheduler.db \
        /opt/cklabScheduler/venv/bin/python -c \
          "from app.database import init_db; init_db()"

8.  Reload systemd unit files (in case unit files changed)
    └── systemctl daemon-reload

9.  Start BOTH services
    ├── systemctl start cklab-scheduler-web
    └── systemctl start cklab-scheduler-worker

10. Health check (same as install step 16)
    ├── sleep 5
    └── curl /api/health → exit 0 on success, exit 1 with rollback hint on failure

    Rollback hint: the backed-up database is at the path printed in step 4.
    Application rollback (code) is a manual step; upgrade.sh does not manage
    a previous-version copy of the code.
```

---

## 15. `deploy/uninstall.sh`

Interactive. Runs as root. Asks before deleting configuration or data.

```
1.  Confirm: "This will remove cklabScheduler. Continue? [y/N]"
2.  Stop and disable services
      systemctl stop    cklab-scheduler-{web,worker} 2>/dev/null
      systemctl disable cklab-scheduler-{web,worker} 2>/dev/null
3.  Remove systemd units
      rm -f /etc/systemd/system/cklab-scheduler-{web,worker}.service
      systemctl daemon-reload
4.  Remove Apache config
      a2dissite cklabscheduler 2>/dev/null
      rm -f /etc/apache2/sites-available/cklabscheduler.conf
      systemctl reload apache2
5.  Ask: "Remove application code at /opt/cklabScheduler? [y/N]"
      If yes: rm -rf /opt/cklabScheduler
6.  Ask: "Remove configuration at /etc/cklabScheduler? [y/N]"
      Warn: "This contains Pexip credentials and O365 secrets."
      If yes: rm -rf /etc/cklabScheduler
7.  Ask: "Remove database and meeting data at /var/lib/cklabScheduler? [y/N]"
      Warn: "THIS IS PERMANENT. All scheduled and historical meeting data will be lost."
      If yes: rm -rf /var/lib/cklabScheduler
8.  Ask: "Remove service account 'cklabscheduler'? [y/N]"
      If yes: userdel cklabscheduler
9.  Print summary of what was and was not removed
```

---

## 16. Migration from Existing Deployment

Migration is handled interactively within `install.sh` (step 8 above). No manual interruption required.

The install script will:
1. Prompt for an optional path to an existing `scheduler.db`
2. Validate it is a readable SQLite database with the expected schema
3. Back it up in place as `scheduler.db.pre-install-TIMESTAMP.bak`
4. Copy it to `/var/lib/cklabScheduler/scheduler.db`
5. Set ownership to `cklabscheduler:cklabscheduler`
6. Continue to step 12 (`init_db()`) which adds the new `scheduler_heartbeat` table and enables WAL mode without touching existing data

The `meetings`, `meeting_endpoints`, and `meeting_invitees` tables are schema-compatible between the old and new application. No column changes. No data conversion required.

---

## 17. `requirements.txt` (updated)

```
Flask==3.0.3
requests==2.32.3
APScheduler==3.10.4
python-dotenv==1.0.1
```

`python-dotenv` is added. In production, systemd's `EnvironmentFile=` provides environment variables before the process starts; `load_dotenv()` is a no-op when variables are already in the environment. In development, it loads from a local `.env` file.

`zoneinfo` is Python 3.12 standard library. Ubuntu 24.04 ships Python 3.12. The system `python3-tzdata` package provides timezone data; the install script installs it.

---

## 18. Security Posture

**Unchanged from current** (per requirement):
- No authentication mechanism added
- Application retains its current open-access behavior
- Code structure supports adding auth later: all routes are in blueprints, `create_app()` is a factory

**Improved from current:**
- Credentials never appear in source code
- `VERIFY_TLS` defaults to `true` (was `false`)
- `HOST_PIN` has no default (was `"2024"`)
- Config file is `640` (not world-readable)
- Gunicorn binds to `127.0.0.1` only (not `0.0.0.0`)
- Service account has no shell and no home directory
- `SECRET_KEY` is a generated 256-bit random value, stable across restarts
- `/api/health` exposes no infrastructure details

---

## 19. What Remains Out of Scope

- UI redesign or frontend behavior changes
- WebSocket / server-sent events (replacing the polling model)
- Per-meeting host PIN support
- Conflict detection / overlap protection
- Recurrence
- Audit log / history panel
- Pagination for large meeting lists
- Timezone-aware day boundary in `meetings_for_day`
- Making `POLL_SECONDS` actually control the frontend polling interval
- Self-hosting Inter font (currently from Google Fonts CDN)
- Authentication / access control
- Let's Encrypt / certbot integration
- Multi-node / HA deployment

---

## 20. File Checklist for Implementation

| File | Action | Notes |
|---|---|---|
| `app/__init__.py` | New | Flask factory, no APScheduler |
| `app/config.py` | New | Settings class, `validate_web()`, `validate_worker()` |
| `app/database.py` | New | `init_db()`, `db()` with WAL/FK/busy_timeout pragmas |
| `app/pexip.py` | New | `PexipAPI` class, remove dead `_command_request` |
| `app/email_service.py` | New | O365/Graph/ICS functions |
| `app/meeting_utils.py` | New | Meeting helpers, updated `classify_meeting()` |
| `app/scheduler_jobs.py` | New | `start_due_meetings()`, `end_due_meetings()`, `expire_missed_meetings()`, `recover_stuck_meetings()`, `scheduler_tick()` |
| `app/routes/__init__.py` | New | |
| `app/routes/ui.py` | New | `GET /` |
| `app/routes/meetings.py` | New | All meeting CRUD routes |
| `app/routes/endpoints.py` | New | `/api/endpoints`, `/api/config` |
| `app/routes/export.py` | New | Export routes |
| `app/routes/health.py` | New | `/api/health` with heartbeat read |
| `app/templates/index.html` | Move | Content unchanged |
| `app/static/app.js` | Move | Content unchanged |
| `app/static/styles.css` | Move | Content unchanged |
| `wsgi.py` | New | Replaces `app.wsgi` |
| `worker.py` | New | Standalone scheduler with heartbeat write, signal handlers |
| `requirements.txt` | Modify | Add `python-dotenv` |
| `.env.example` | Modify | Add all missing O365 vars, `SECRET_KEY`, remove sensitive defaults |
| `deploy/install.sh` | New | Interactive installer per §13 |
| `deploy/upgrade.sh` | New | Upgrade script per §14 |
| `deploy/uninstall.sh` | New | Uninstaller per §15 |
| `app.py` | Delete | All logic redistributed to modules above |
| `app.wsgi` | Delete | Replaced by `wsgi.py` |
| `README.md` | Update | Reflect new structure, deployment method, service names |
| `REBUILD_PLAN.md` | This file | Architecture reference; not shipped to server |
