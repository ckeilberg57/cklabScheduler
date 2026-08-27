# Phase 3 — Fresh-Install Validation Plan

## Overview

This plan validates a complete cklabScheduler deployment on a blank Ubuntu 24.04
server. Follow each phase in order. Every section lists exact commands, expected
output, and a pass/fail criterion.

**Before you start:** Ensure you have a functioning Pexip deployment with at least
one registered endpoint and Management API credentials. You cannot complete the
meeting lifecycle tests without a live Pexip system.

**Placeholders used throughout:**

| Placeholder | Replace with |
|---|---|
| `<SERVER>` | Hostname or IP of your Ubuntu 24.04 server |
| `<PEXIP_MGR>` | Pexip Management Node hostname |
| `<PEXIP_EDGE>` | Pexip Command/Edge Node hostname |
| `<MGMT_USER>` | Pexip Management API username |
| `<ENDPOINT_ALIAS>` | Registered endpoint alias, e.g. `boardroom@pexip.example.com` |

---

## 3.0 Preparation — Copying the Project to the Server

### 3.0.1 Archive the repository

On your local machine (where the repository lives):

```bash
cd /path/to/cklabScheduler-rebuild

# Create a clean archive — excludes venv, databases, and dev artifacts
tar --create --gzip \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.db' \
    --exclude='.env' \
    --exclude='.env.original' \
    --exclude='.DS_Store' \
    --exclude='.pytest_cache' \
    --file=/tmp/cklabScheduler.tar.gz \
    .
```

### 3.0.2 Copy the archive to the server

```bash
scp /tmp/cklabScheduler.tar.gz <SERVER>:/tmp/
```

### 3.0.3 Extract on the server

```bash
ssh root@<SERVER>
mkdir -p /root/cklabScheduler-src
tar -xzf /tmp/cklabScheduler.tar.gz -C /root/cklabScheduler-src
ls /root/cklabScheduler-src/deploy/
```

**Expected:** `install.sh  upgrade.sh  uninstall.sh  verify_install.sh  TEST_PLAN.md
cklab-scheduler-web.service  cklab-scheduler-worker.service  cklabscheduler.conf`

---

## 3.1 Installation — Running install.sh

### 3.1.1 Run the installer

```bash
cd /root/cklabScheduler-src
bash deploy/install.sh
```

### 3.1.2 Respond to prompts

Work through each prompt. Refer to the table below for expected inputs:

| Prompt | Input |
|---|---|
| Pexip Registration/Status host | `<PEXIP_MGR>` |
| Pexip Command/Edge host | `<PEXIP_EDGE>` |
| Pexip Management API username | `<MGMT_USER>` |
| Pexip Management API password | *(enter password; not echoed)* |
| Verify TLS | `true` |
| Conference host PIN | *(press Enter for no PIN)* |
| Scheduler display name | `Scheduler` *(or press Enter for default)* |
| Dial protocol | `auto` *(or press Enter)* |
| WebRTC base URL | *(press Enter to construct from COMMAND_HOST)* |
| Minutes before start | `1` |
| Default extension minutes | `15` |
| Frontend poll interval | `3` |
| Enable O365? | `N` |
| Server hostname | `<SERVER>` |
| TLS option | `2` *(self-signed for validation; use 1 in production)* |
| Migrate existing scheduler.db? | `N` |

### 3.1.3 Expected install output

The installer should complete without errors and print:

```
[16/16] Health check
  Waiting for services to initialise...

  ✓ Installation complete.

  Application URL : https://<SERVER>/cklabScheduler/
  Health endpoint : https://<SERVER>/cklabScheduler/api/health
```

**Pass criterion:** No `FATAL:` lines. Health check reports `✓ Installation complete.`

---

## 3.2 Automated Post-Install Checks

Run `verify_install.sh` immediately after installation:

```bash
sudo bash /root/cklabScheduler-src/deploy/verify_install.sh <SERVER>
```

**Expected output:** All checks `✓`, final line `ALL CHECKS PASSED`, exit code 0.

The script validates:
- Ubuntu 24.04 platform
- Service account (`cklabscheduler`, nologin shell)
- Directory existence, ownership, and permissions
- Configuration file permissions (640) and required keys
- Application files present; dev artifacts absent
- Python venv with all five packages
- Both systemd services active and enabled
- Port 5080 bound to 127.0.0.1
- Apache: site enabled, ProxyPass with prefix preserved (`/cklabScheduler/ → :5080/cklabScheduler/`), RedirectMatch, modules
- Health endpoint returns HTTP 200 with `ok=true`
- SQLite: WAL mode, four tables, fresh heartbeat

**Pass criterion:** `verify_install.sh` exits 0 with zero failures.

---

## 3.3 Smoke Test — UI and API

### 3.3.1 Load the application in a browser

Navigate to `https://<SERVER>/cklabScheduler/`

**Expected:**
- Browser follows redirect from `/cklabScheduler` to `/cklabScheduler/`
- TLS warning appears (expected for self-signed cert)
- Scheduler UI loads with the meeting calendar

**Pass criterion:** UI renders without JavaScript console errors.

### 3.3.2 Bare path redirect

```bash
curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' \
    "https://<SERVER>/cklabScheduler"
```

**Expected:** `301 https://<SERVER>/cklabScheduler/`

### 3.3.3 Health endpoint

> **Architecture note — ProxyPass prefix preservation (r3 fix)**
>
> Apache forwards `/cklabScheduler/...` to `http://127.0.0.1:5080/cklabScheduler/...`
> (prefix preserved). Gunicorn's `--env SCRIPT_NAME=/cklabScheduler` strips the prefix
> before Flask sees the request. If the backend URL were `http://127.0.0.1:5080/` instead,
> Gunicorn would receive `/api/health` without the prefix, fail to split on `/cklabScheduler`,
> and return HTTP 500 (IndexError in gunicorn/http/wsgi.py) before Flask is reached.
>
> **Regression check** — both of these must succeed:
> ```bash
> # Through Apache (browser-style path)
> curl -sk "https://<SERVER>/cklabScheduler/api/health" | python3 -m json.tool
> # Direct to Gunicorn using the preserved path (as Apache forwards it)
> curl -s  "http://127.0.0.1:5080/cklabScheduler/api/health"
> ```
> The second curl must also return valid JSON, not a 500. If it returns 500, the
> `deploy/cklab-scheduler-web.service` `SCRIPT_NAME` or the Apache ProxyPass is misconfigured.

```bash
curl -sk "https://<SERVER>/cklabScheduler/api/health" | python3 -m json.tool
```

**Expected:**

```json
{
    "ok": true,
    "service": "cklabScheduler",
    "version": "2.0.0",
    "database": { "ok": true, "meeting_count": 0 },
    "pexip": { "configured": true },
    "o365": { "enabled": false, "configured": false },
    "scheduler_worker": { "ok": true, "last_heartbeat_seconds_ago": <N> }
}
```

**Pass criteria:**
- `ok` is `true`
- `scheduler_worker.ok` is `true`
- `last_heartbeat_seconds_ago` is less than 30
- No Pexip hostnames or credentials appear anywhere in the response body

### 3.3.4 Endpoint registration listing

```bash
curl -sk "https://<SERVER>/cklabScheduler/api/endpoints" | python3 -m json.tool | head -30
```

**Expected:**
- HTTP 200
- `"ok": true`
- `"endpoints"` array contains Pexip-registered endpoints from `<PEXIP_MGR>`

If the response is `{"ok": false, "error": "..."}`, the Management API credentials
or `REG_STATUS_HOST` are incorrect. Check `/etc/cklabScheduler/cklabScheduler.env`
and restart the web service after correcting.

**Pass criterion:** At least one endpoint appears in the list.

### 3.3.5 Config endpoint

```bash
curl -sk "https://<SERVER>/cklabScheduler/api/config" | python3 -m json.tool
```

**Expected:** `"ok": true`, `"pattern_regex": "^doc[a-zA-Z0-9]{16}$"`, non-empty
`"command_host"` field.

---

## 3.4 Meeting Lifecycle Test

This section creates a real meeting and verifies the complete state machine:
`scheduled → starting → started → ending → ended`.

**Requirements:**
- A registered Pexip endpoint whose alias is `<ENDPOINT_ALIAS>`
- The meeting alias must match `^doc[a-zA-Z0-9]{16}$` (19 chars total)

### 3.4.1 Set timing variables

On the **server** as root:

```bash
ALIAS="docPHASE3VALIDATE"    # 19 chars: doc + 16 alphanumeric
START=$(date -u -d '+90 seconds' '+%Y-%m-%dT%H:%M:%S+00:00')
END=$(date -u -d '+6 minutes'   '+%Y-%m-%dT%H:%M:%S+00:00')
echo "Start: ${START}"
echo "End:   ${END}"
```

90 seconds gives you time to verify the `scheduled` state before the worker
claims it. 6 minutes gives enough time to observe the full lifecycle.

### 3.4.2 Create the test meeting

```bash
curl -sk -X POST "https://<SERVER>/cklabScheduler/api/meetings" \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"Phase 3 Validation Test\",
    \"meeting_alias\": \"${ALIAS}\",
    \"start_time\": \"${START}\",
    \"end_time\": \"${END}\",
    \"notes\": \"Automated Phase 3 test — safe to delete\",
    \"endpoints\": [
      {
        \"endpoint_alias\": \"<ENDPOINT_ALIAS>\",
        \"display_name\": \"Test Endpoint\",
        \"role\": \"host\"
      }
    ]
  }" | python3 -m json.tool
```

**Expected:** `"ok": true` with a meeting object containing:
- `"status": "scheduled"`
- `"started_at": null`
- `"ended_at": null`

### 3.4.3 Confirm meeting written to SQLite

```bash
sqlite3 /var/lib/cklabScheduler/scheduler.db \
  "SELECT id, meeting_alias, status, started_at, ended_at
   FROM meetings
   WHERE meeting_alias = '${ALIAS}';"
```

**Expected:**

```
1|docPHASE3VALIDATE|scheduled||
```

`started_at` and `ended_at` are blank (NULL). Status is `scheduled`.

**Pass criterion:** Row is present with `status = scheduled`, `started_at` is NULL.

### 3.4.4 Confirm worker claim (starting state)

Wait until the start time passes, then watch for the transition. The worker
ticks every 10 seconds; allow up to 15 seconds after `START` for the claim.

```bash
# Watch status in real time
watch -n 2 "sqlite3 /var/lib/cklabScheduler/scheduler.db \
  \"SELECT status, started_at FROM meetings WHERE meeting_alias = '${ALIAS}';\""
```

**Expected sequence:**
1. `scheduled|` (before start time)
2. `starting|` (within 10s of start time — claim committed before Pexip calls)
3. `started|2024-...` or `started_with_errors|2024-...` (after dial completes)

**Pass criterion:** Status transitions through `starting` then reaches `started`
or `started_with_errors`. `started_at` is populated.

### 3.4.5 Confirm Pexip dial-out in journal

```bash
journalctl -u cklab-scheduler-worker \
    --since "$(date -u -d '-2 minutes' '+%Y-%m-%d %H:%M:%S')" \
    --no-pager | grep -E 'start|dial|started'
```

**Expected log lines** (representative):

```
cklab-scheduler-worker[NNN]: Meeting docPHASE3VALIDATE (id=1) started as started
```

Or if any dial failed:

```
cklab-scheduler-worker[NNN]: Start: dial failed for <ENDPOINT_ALIAS> in docPHASE3VALIDATE: ...
cklab-scheduler-worker[NNN]: Meeting docPHASE3VALIDATE (id=1) started as started_with_errors
```

**Pass criterion:** A journal line contains `started as started` or
`started as started_with_errors` for the test meeting alias.

### 3.4.6 Confirm endpoint status in SQLite

```bash
sqlite3 /var/lib/cklabScheduler/scheduler.db \
  "SELECT me.endpoint_alias, me.status, me.dial_response
   FROM meeting_endpoints me
   JOIN meetings m ON m.id = me.meeting_id
   WHERE m.meeting_alias = '${ALIAS}';"
```

**Expected:** `<ENDPOINT_ALIAS>|dialed|{"result": ...}` (or `error` if dial failed)

**Pass criterion:** Endpoint row has `status = dialed` or `status = error`
(never remains `scheduled` after the meeting reaches `started`/`started_with_errors`).

### 3.4.7 Confirm meeting visible in UI

Navigate to `https://<SERVER>/cklabScheduler/` and select today's date.

**Expected:** Meeting "Phase 3 Validation Test" appears. Status badge shows
`started` (or `started_with_errors`).

**Pass criterion:** Meeting is visible in the UI with the correct status.

### 3.4.8 Confirm scheduled disconnect at meeting end

After `END` time passes, allow up to 15 seconds for the worker to claim the ending:

```bash
sqlite3 /var/lib/cklabScheduler/scheduler.db \
  "SELECT status, started_at, ended_at
   FROM meetings
   WHERE meeting_alias = '${ALIAS}';"
```

**Expected sequence:**
1. `ended_at` is NULL while meeting is active
2. `ending|<started_at>|` (transition state, very brief)
3. `ended|<started_at>|<ended_at>` or `ended_with_errors|...`

```bash
journalctl -u cklab-scheduler-worker \
    --since "$(date -u -d '-1 minute' '+%Y-%m-%d %H:%M:%S')" \
    --no-pager | grep -E 'end|disconnect'
```

**Expected:** `Meeting docPHASE3VALIDATE (id=1) ended as ended`

**Pass criterion:** Final status is `ended` or `ended_with_errors`. `ended_at` is
populated. UI shows status badge `ended`.

---

## 3.5 Worker Resilience — Restart During Active Meeting

This test verifies crash recovery. Create a fresh meeting that will be active
during the test.

### 3.5.1 Create a long-running test meeting

```bash
ALIAS2="docPHASE3RESTART0"   # 19 chars
START2=$(date -u -d '+30 seconds' '+%Y-%m-%dT%H:%M:%S+00:00')
END2=$(date -u -d '+10 minutes'  '+%Y-%m-%dT%H:%M:%S+00:00')

curl -sk -X POST "https://<SERVER>/cklabScheduler/api/meetings" \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"Phase 3 Restart Test\",
    \"meeting_alias\": \"${ALIAS2}\",
    \"start_time\": \"${START2}\",
    \"end_time\": \"${END2}\",
    \"endpoints\": [{
      \"endpoint_alias\": \"<ENDPOINT_ALIAS>\",
      \"display_name\": \"Test Endpoint\",
      \"role\": \"host\"
    }]
  }" | python3 -m json.tool
```

### 3.5.2 Wait for meeting to reach `started` state

```bash
# Poll until started
until sqlite3 /var/lib/cklabScheduler/scheduler.db \
    "SELECT status FROM meetings WHERE meeting_alias='${ALIAS2}';" \
    | grep -qE 'started'; do
  sleep 3
  echo -n "."
done
echo " started"
```

### 3.5.3 Force-restart the worker

```bash
systemctl restart cklab-scheduler-worker
echo "Worker restarted at $(date -u)"
```

### 3.5.4 Verify recovery behaviour

Wait ~15 seconds (worker needs 2 minutes to consider a meeting "stuck" — but
immediately after restart, `recover_stuck_meetings()` sees the meeting in `started`
state with a valid `started_at`, so it is **not** treated as stuck). The meeting
should remain `started`:

```bash
sqlite3 /var/lib/cklabScheduler/scheduler.db \
  "SELECT status, started_at FROM meetings WHERE meeting_alias='${ALIAS2}';"
```

**Expected:** `started|<timestamp>` — status unchanged. The worker does not
re-claim or re-dial a meeting that is already in `started` state; recovery
only acts on `starting` and `ending` transitions.

### 3.5.5 Verify heartbeat resumes within 30 seconds

```bash
sleep 15
curl -sk "https://<SERVER>/cklabScheduler/api/health" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); \
      print('heartbeat:', d['scheduler_worker']['last_heartbeat_seconds_ago'], 's ago'); \
      print('ok:', d['scheduler_worker']['ok'])"
```

**Expected:** `ok: True`, heartbeat age < 30 seconds.

### 3.5.6 Simulate a crash mid-start (stuck `starting` state)

Manually set a meeting to `starting` with an old `updated_at`:

```bash
ALIAS3="docPHASE3RECOVERY0"
sqlite3 /var/lib/cklabScheduler/scheduler.db \
  "INSERT INTO meetings
     (title, meeting_alias, start_time, end_time, status, updated_at, created_at)
   VALUES
     ('Recovery Test', '${ALIAS3}',
      datetime('now', '-5 minutes'), datetime('now', '+5 minutes'),
      'starting', datetime('now', '-3 minutes'), datetime('now', '-10 minutes'));"
```

Wait up to 15 seconds (next tick) and observe:

```bash
sleep 15
sqlite3 /var/lib/cklabScheduler/scheduler.db \
  "SELECT status FROM meetings WHERE meeting_alias='${ALIAS3}';"
```

**Expected:** `started` or `started_with_errors` — `recover_stuck_meetings()`
detected the 3-minute-old `starting` state and attempted recovery.

```bash
journalctl -u cklab-scheduler-worker --since "30 seconds ago" --no-pager \
  | grep -i 'recov'
```

**Expected log line:** `Recovery: meeting docPHASE3RECOVERY0 recovered as started[_with_errors]`

**Pass criterion:** Stuck `starting` meeting is resolved without manual
intervention. Journal shows recovery log entry.

---

## 3.6 Upgrade Test

This test verifies that `upgrade.sh` replaces code, preserves config and data,
and leaves both services running.

### 3.6.1 Record pre-upgrade state

```bash
# Record env file modification time
stat -c '%y' /etc/cklabScheduler/cklabScheduler.env

# Record database row count
sqlite3 /var/lib/cklabScheduler/scheduler.db "SELECT COUNT(*) FROM meetings;"

# Record the current SECRET_KEY (first 8 chars only — do not expose full key)
grep '^SECRET_KEY=' /etc/cklabScheduler/cklabScheduler.env | cut -c1-20

# Record current Apache ProxyPass target (should change after r2 → r3 upgrade)
grep -E 'ProxyPass[^R]' /etc/apache2/sites-available/cklabscheduler.conf
```

For an r2 installation the ProxyPass line will show:
```
    ProxyPass        /cklabScheduler/ http://127.0.0.1:5080/
```
After upgrade it must show `http://127.0.0.1:5080/cklabScheduler/`.

### 3.6.2 Simulate a code update

Make a trivial, visible change to any source file on your local machine:

```bash
# On local machine — add a comment to worker.py
echo '# Phase 3 upgrade test marker' >> worker.py
git add worker.py
git commit -m "chore: phase 3 upgrade test marker"
```

Re-package and copy the updated archive to the server (repeat step 3.0.1–3.0.2).
Extract to a new directory:

```bash
# On server
mkdir -p /root/cklabScheduler-v2
tar -xzf /tmp/cklabScheduler.tar.gz -C /root/cklabScheduler-v2
```

### 3.6.3 Run upgrade.sh

```bash
cd /root/cklabScheduler-v2
bash deploy/upgrade.sh
```

**Expected output (abridged):**

```
══ Pre-flight ══
  Existing install confirmed.
══ Stopping services ══
  Both services stopped.
══ Backing up database ══
  Backup: /var/lib/cklabScheduler/scheduler.db.bak.<TIMESTAMP>
══ Replacing application files ══
  Files replaced...
══ Updating Python dependencies ══
  Dependencies updated.
══ Running database migrations ══
  Schema up to date.
══ Updating systemd unit files ══
  Unit files updated and daemon reloaded.
══ Updating Apache configuration ══
  Detected r2 ProxyPass (http://127.0.0.1:5080/) — applying r3 migration...
  Backup: /etc/apache2/sites-available/cklabscheduler.conf.bak.<TIMESTAMP>
  ProxyPass lines updated. Validating new configuration...
  Syntax OK
  Apache configuration validated and reloaded.
══ Starting services ══
  Both services started.
══ Health check ══
  ✓ Upgrade complete.
```

If the system already had the r3 ProxyPass (e.g., a repeated upgrade), the Apache step
will instead print:
```
══ Updating Apache configuration ══
  ProxyPass already uses r3 prefix-preserved format — no change needed.
```

### 3.6.4 Verify post-upgrade state

```bash
# Config file must be unchanged
stat -c '%y' /etc/cklabScheduler/cklabScheduler.env
# (modification time must match pre-upgrade timestamp)

# Database row count must match
sqlite3 /var/lib/cklabScheduler/scheduler.db "SELECT COUNT(*) FROM meetings;"

# SECRET_KEY prefix must match
grep '^SECRET_KEY=' /etc/cklabScheduler/cklabScheduler.env | cut -c1-20

# Code change visible in installed file
grep 'Phase 3 upgrade test marker' /opt/cklabScheduler/worker.py

# Services running
systemctl is-active cklab-scheduler-web cklab-scheduler-worker

# Health check clean
curl -sk "https://<SERVER>/cklabScheduler/api/health" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok:', d['ok'])"
```

**Pass criteria:**
- `cklabScheduler.env` modification time is **unchanged** from pre-upgrade
- Meeting row count is **unchanged**
- `SECRET_KEY` prefix is **unchanged** (proving the env file was not overwritten)
- Code marker is present in `/opt/cklabScheduler/worker.py`
- Both services are `active`
- Health check returns `ok: True`

### 3.6.5 Verify database backup was created

```bash
ls -lh /var/lib/cklabScheduler/scheduler.db.bak.*
```

**Expected:** One `.bak.<TIMESTAMP>` file created just before the upgrade.

### 3.6.6 Verify Apache ProxyPass migration (r2 → r3)

Confirm the Apache config was surgically updated:

```bash
# ProxyPass target must now include the prefix
grep -E 'ProxyPass[^R]' /etc/apache2/sites-available/cklabscheduler.conf
```

**Expected (r3 format):**
```
    ProxyPass        /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/
```

Confirm the Apache config backup was created:

```bash
ls -lh /etc/apache2/sites-available/cklabscheduler.conf.bak.*
```

**Expected:** One `.bak.<TIMESTAMP>` file from the upgrade run.

Test that the corrected path reaches Gunicorn directly (bypassing Apache):

```bash
# Apache forwards this exact path; Gunicorn must respond with valid JSON
curl -s http://127.0.0.1:5080/cklabScheduler/api/health
```

**Expected:** `{"ok": true, ...}` — not an HTTP 500 / IndexError.

Run `verify_install.sh` to confirm all checks still pass:

```bash
sudo bash /root/cklabScheduler-v2/deploy/verify_install.sh <SERVER>
```

**Pass criteria:**
- `ProxyPass /cklabScheduler/ → 127.0.0.1:5080/cklabScheduler/ (prefix preserved)` ✓
- Apache config backup file exists under `sites-available/`
- Direct Gunicorn curl returns `"ok": true`
- `verify_install.sh` exits 0

### 3.6.7 Apache rollback on configtest failure (optional destructive test)

To verify the automatic rollback path, temporarily corrupt the Apache config after
backing it up, trigger `upgrade.sh`, and observe the restore:

```bash
# On server — simulate a broken config BEFORE running upgrade from a clean r2 state
# WARNING: this test intentionally breaks Apache temporarily; restore immediately after.

# Manually corrupt the config
echo 'InvalidDirective' >> /etc/apache2/sites-available/cklabscheduler.conf

# Run upgrade from an r2 snapshot (requires resetting ProxyPass to r2 first)
# Then observe upgrade.sh output — it must print:
#   ERROR: apache2ctl configtest failed — restoring backup.
#   FATAL: Apache configuration migration failed. Restored from <backup>.

# Verify the file was restored
apache2ctl configtest   # must return Syntax OK
```

**Pass criterion:** upgrade.sh exits non-zero, prints a `FATAL:` message naming the
backup path, and the live Apache config is identical to the pre-migration state.

---

## 3.7 Uninstall Test — Preserve Config and Data

This test verifies that `uninstall.sh` correctly removes services and code while
defaulting to preserving configuration, database, and the service account.

### 3.7.1 Record state before uninstall

```bash
stat /etc/cklabScheduler/cklabScheduler.env   # must survive
stat /var/lib/cklabScheduler/scheduler.db     # must survive
id cklabscheduler                             # must survive
```

### 3.7.2 Run uninstall.sh

```bash
bash /root/cklabScheduler-v2/deploy/uninstall.sh
```

Answer each prompt as follows:

| Prompt | Answer | Intent |
|---|---|---|
| `Continue with uninstall?` | `y` | Proceed |
| `Remove application code at /opt/cklabScheduler?` | `y` | Remove code |
| `Remove configuration at /etc/cklabScheduler?` | `n` | **Preserve config** |
| `Remove database data at /var/lib/cklabScheduler?` | `n` | **Preserve data** |
| `Remove service account 'cklabscheduler'?` | `n` | **Preserve account** |

### 3.7.3 Verify post-uninstall state

```bash
# Services must be gone
systemctl is-active cklab-scheduler-web   2>/dev/null || echo "stopped (expected)"
systemctl is-active cklab-scheduler-worker 2>/dev/null || echo "stopped (expected)"

# Unit files must be removed
test ! -f /etc/systemd/system/cklab-scheduler-web.service    && echo "unit removed (expected)"
test ! -f /etc/systemd/system/cklab-scheduler-worker.service && echo "unit removed (expected)"

# Apache site must be removed
test ! -f /etc/apache2/sites-available/cklabscheduler.conf && echo "apache config removed (expected)"
test ! -L /etc/apache2/sites-enabled/cklabscheduler.conf   && echo "apache site disabled (expected)"

# Application code must be gone
test ! -d /opt/cklabScheduler && echo "app code removed (expected)"

# Configuration must be preserved
test -f /etc/cklabScheduler/cklabScheduler.env && echo "config preserved (expected)"

# Database must be preserved
test -f /var/lib/cklabScheduler/scheduler.db && echo "database preserved (expected)"

# Service account must be preserved
id cklabscheduler && echo "service account preserved (expected)"
```

### 3.7.4 Verify uninstall summary output

The final output should list exactly what was removed and what was kept:

```
══ Uninstall summary ══

  Removed:
    ✓ systemd units
    ✓ Apache vhost config
    ✓ application code (/opt/cklabScheduler)

  Preserved:
    – configuration (/etc/cklabScheduler)
    – database data (/var/lib/cklabScheduler)
    – service account 'cklabscheduler'
```

**Pass criteria:**
- Services are stopped and unit files removed
- Apache site is removed
- `/opt/cklabScheduler` does not exist
- `/etc/cklabScheduler/cklabScheduler.env` exists and is unchanged
- `/var/lib/cklabScheduler/scheduler.db` exists and is unchanged
- `cklabscheduler` user still exists

---

## Final Pass/Fail Checklist

Mark each item ✓ PASS, ✗ FAIL, or N/A before signing off on Phase 3.

### Preparation and Installation

- [ ] 3.0 — Project archived and copied without dev artifacts
- [ ] 3.1 — `install.sh` completed without `FATAL:` errors
- [ ] 3.1 — Health check reported `✓ Installation complete.`

### Automated Verification

- [ ] 3.2 — `verify_install.sh` exits 0 with zero failures

### Smoke Tests

- [ ] 3.3.1 — UI loads at `/cklabScheduler/`
- [ ] 3.3.2 — Bare `/cklabScheduler` redirects 301 → `/cklabScheduler/`
- [ ] 3.3.3 — `/api/health` returns `ok: true`, `scheduler_worker.ok: true`
- [ ] 3.3.3 — Health response exposes no hostnames or credentials
- [ ] 3.3.4 — `/api/endpoints` returns at least one Pexip endpoint
- [ ] 3.3.5 — `/api/config` returns expected pattern and non-empty `command_host`

### Meeting Lifecycle

- [ ] 3.4.1 — Meeting created with `ok: true`
- [ ] 3.4.3 — SQLite shows `status = scheduled`, `started_at = NULL`
- [ ] 3.4.4 — Worker transitions through `starting` → `started[_with_errors]`
- [ ] 3.4.5 — Journal shows dial-out log entry for the test alias
- [ ] 3.4.6 — Endpoint row has `status = dialed` or `error` in SQLite
- [ ] 3.4.7 — Meeting visible in UI with correct status badge
- [ ] 3.4.8 — Meeting transitions to `ended[_with_errors]` after end time
- [ ] 3.4.8 — `ended_at` is populated in SQLite

### Worker Resilience

- [ ] 3.5.4 — Worker restart does not re-claim active `started` meeting
- [ ] 3.5.5 — Heartbeat resumes within 30 seconds of restart
- [ ] 3.5.6 — Stuck `starting` meeting recovered automatically
- [ ] 3.5.6 — Recovery journal entry present

### Upgrade

- [ ] 3.6.3 — `upgrade.sh` completed without errors
- [ ] 3.6.3 — Apache migration step printed "validated and reloaded" (r2 → r3)
- [ ] 3.6.4 — `cklabScheduler.env` modification time unchanged
- [ ] 3.6.4 — `SECRET_KEY` unchanged (env file was not overwritten)
- [ ] 3.6.4 — Code change present in `/opt/cklabScheduler/worker.py`
- [ ] 3.6.4 — Both services active post-upgrade
- [ ] 3.6.4 — Health check `ok: True` post-upgrade
- [ ] 3.6.5 — Database backup file created
- [ ] 3.6.6 — Apache ProxyPass shows `http://127.0.0.1:5080/cklabScheduler/` (r3 format)
- [ ] 3.6.6 — Apache config backup created under `sites-available/`
- [ ] 3.6.6 — Direct `curl http://127.0.0.1:5080/cklabScheduler/api/health` returns `ok: true`
- [ ] 3.6.6 — `verify_install.sh` passes ProxyPass check post-upgrade

### Uninstall

- [ ] 3.7.3 — Services stopped, unit files removed
- [ ] 3.7.3 — Apache site removed
- [ ] 3.7.3 — `/opt/cklabScheduler` removed
- [ ] 3.7.3 — `/etc/cklabScheduler/cklabScheduler.env` preserved
- [ ] 3.7.3 — `/var/lib/cklabScheduler/scheduler.db` preserved
- [ ] 3.7.3 — `cklabscheduler` user account preserved

---

*Sign-off:* _________________ Date: _________________
