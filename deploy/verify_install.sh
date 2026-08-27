#!/usr/bin/env bash
# deploy/verify_install.sh — Read-only post-install verification for cklabScheduler
#
# Inspects files, permissions, services, ports, Apache, health endpoint, and SQLite.
# DOES NOT modify any configuration, create/delete meetings, or change service state.
#
# Usage:
#   sudo bash deploy/verify_install.sh [HOSTNAME]
#
#   HOSTNAME — optional; server hostname used in the health endpoint URL.
#              Defaults to "localhost" (works for self-signed certs via --insecure).

# No set -e: every check must run to completion regardless of prior failures.
set -uo pipefail

# ── Constants ────────────────────────────────────────────────────────────────
APP_DIR="/opt/cklabScheduler"
CONF_DIR="/etc/cklabScheduler"
DATA_DIR="/var/lib/cklabScheduler"
ENV_FILE="${CONF_DIR}/cklabScheduler.env"
DB_PATH="${DATA_DIR}/scheduler.db"
VENV="${APP_DIR}/venv"
SVC_USER="cklabscheduler"
WEB_SVC="cklab-scheduler-web"
WORKER_SVC="cklab-scheduler-worker"
HOSTNAME_ARG="${1:-localhost}"
HEALTH_URL="https://${HOSTNAME_ARG}/cklabScheduler/api/health"

# ── Counters and helpers ─────────────────────────────────────────────────────
PASS=0
FAIL=0
WARN=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*";     PASS=$(( PASS + 1 )); }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; FAIL=$(( FAIL + 1 )); }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$*";     WARN=$(( WARN + 1 )); }
skip() { printf '  \033[2m–\033[0m %s\n' "$*"; }
hdr()  { printf '\n\033[1m[%s]\033[0m\n' "$*"; }

# Run a command silently; report ok/fail based on exit code.
chk() {
    local desc="$1"; shift
    if "$@" &>/dev/null; then
        ok "${desc}"
    else
        fail "${desc}"
    fi
}

# stat helpers (Linux / Ubuntu)
stat_perm()  { stat -c '%a'   "$1" 2>/dev/null; }
stat_owner() { stat -c '%U:%G' "$1" 2>/dev/null; }

# ── Root check ───────────────────────────────────────────────────────────────
printf '\n\033[1m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1m  cklabScheduler — Installation Verification\033[0m\n'
printf '\033[1m══════════════════════════════════════════════════════════\033[0m\n'

if [[ "${EUID}" -ne 0 ]]; then
    warn "Not running as root — checks requiring root will be skipped or may fail."
    warn "Re-run with: sudo bash deploy/verify_install.sh ${HOSTNAME_ARG}"
fi

# ── 1. Platform ───────────────────────────────────────────────────────────────
hdr "Platform"
if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    if [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]]; then
        ok "Ubuntu 24.04 (${PRETTY_NAME:-})"
    else
        fail "Expected Ubuntu 24.04; detected: ${PRETTY_NAME:-unknown}"
    fi
else
    fail "/etc/os-release not found"
fi

# ── 2. Service account ────────────────────────────────────────────────────────
hdr "Service Account"
if id "${SVC_USER}" &>/dev/null; then
    ok "User '${SVC_USER}' exists"
    SHELL_VAL="$(getent passwd "${SVC_USER}" | cut -d: -f7)"
    if [[ "${SHELL_VAL}" == "/usr/sbin/nologin" ]]; then
        ok "Shell is /usr/sbin/nologin"
    else
        fail "Shell is '${SHELL_VAL}' (expected /usr/sbin/nologin)"
    fi
    HOME_VAL="$(getent passwd "${SVC_USER}" | cut -d: -f6)"
    if [[ "${HOME_VAL}" == "/" || "${HOME_VAL}" == "/nonexistent" || -z "${HOME_VAL}" ]]; then
        ok "No home directory (home: ${HOME_VAL:-unset})"
    else
        warn "Home directory set to '${HOME_VAL}' (expected /nonexistent)"
    fi
else
    fail "User '${SVC_USER}' does not exist"
fi

# ── 3. Directories ────────────────────────────────────────────────────────────
hdr "Directories"
check_dir() {
    local path="$1" expected_owner="$2" expected_perm="$3"
    if [[ -d "${path}" ]]; then
        ok "${path} exists"
        local owner perm
        owner="$(stat_owner "${path}")"
        perm="$(stat_perm "${path}")"
        if [[ "${owner}" == "${expected_owner}" ]]; then
            ok "${path} owner: ${owner}"
        else
            fail "${path} owner: got ${owner}, expected ${expected_owner}"
        fi
        if [[ "${perm}" == "${expected_perm}" ]]; then
            ok "${path} permissions: ${perm}"
        else
            fail "${path} permissions: got ${perm}, expected ${expected_perm}"
        fi
    else
        fail "${path} does not exist"
    fi
}

check_dir "${APP_DIR}"  "root:${SVC_USER}" "750"
check_dir "${CONF_DIR}" "root:${SVC_USER}" "750"
check_dir "${DATA_DIR}" "${SVC_USER}:${SVC_USER}" "750"

# ── 4. Configuration file ────────────────────────────────────────────────────
hdr "Configuration File"
if [[ -f "${ENV_FILE}" ]]; then
    ok "${ENV_FILE} exists"
    ENV_PERM="$(stat_perm "${ENV_FILE}")"
    ENV_OWNER="$(stat_owner "${ENV_FILE}")"
    if [[ "${ENV_PERM}" == "640" ]]; then
        ok "${ENV_FILE} permissions: 640"
    else
        fail "${ENV_FILE} permissions: got ${ENV_PERM}, expected 640"
    fi
    if [[ "${ENV_OWNER}" == "root:${SVC_USER}" ]]; then
        ok "${ENV_FILE} owner: root:${SVC_USER}"
    else
        fail "${ENV_FILE} owner: got ${ENV_OWNER}, expected root:${SVC_USER}"
    fi
    # Verify required keys are present (values are not inspected or printed)
    for key in REG_STATUS_HOST COMMAND_HOST MGMT_USER MGMT_PASS SECRET_KEY DB_PATH; do
        if grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
            ok "  Key present: ${key}"
        else
            fail "  Missing key: ${key}"
        fi
    done
    # Verify SECRET_KEY is not blank
    SK_LINE="$(grep '^SECRET_KEY=' "${ENV_FILE}" 2>/dev/null || true)"
    if [[ "${SK_LINE}" =~ ^SECRET_KEY=\"?\"?$ ]] || [[ "${SK_LINE}" =~ ^SECRET_KEY=\"\"$ ]]; then
        fail "SECRET_KEY is empty — reinstall to regenerate"
    else
        ok "SECRET_KEY is non-empty"
    fi
    # Ensure no world-readable files in CONF_DIR
    WORLD_R=$(find "${CONF_DIR}" -perm /o+r -type f 2>/dev/null | wc -l)
    if [[ "${WORLD_R}" -eq 0 ]]; then
        ok "${CONF_DIR} has no world-readable files"
    else
        fail "${CONF_DIR} contains ${WORLD_R} world-readable file(s)"
    fi
else
    fail "${ENV_FILE} does not exist — installation may be incomplete"
fi

# ── 5. Application files ──────────────────────────────────────────────────────
hdr "Application Files"
for f in \
    wsgi.py \
    worker.py \
    requirements.txt \
    app/__init__.py \
    app/config.py \
    app/database.py \
    app/pexip.py \
    app/scheduler_jobs.py \
    app/routes/meetings.py \
    app/routes/health.py \
    app/static/app.js \
    app/templates/index.html
do
    if [[ -f "${APP_DIR}/${f}" ]]; then
        ok "${f}"
    else
        fail "${f} missing"
    fi
done

# Confirm deploy/ and .env.original were not copied to server
for artifact in deploy .env.original .git tests; do
    if [[ -e "${APP_DIR}/${artifact}" ]]; then
        fail "Artifact present on server: ${APP_DIR}/${artifact}"
    else
        ok "Artifact absent (correct): ${artifact}"
    fi
done

# Spot-check file permissions in app/
WORLD_APP=$(find "${APP_DIR}" -not -path "${VENV}/*" -perm /o+r -type f 2>/dev/null | wc -l)
if [[ "${WORLD_APP}" -eq 0 ]]; then
    ok "${APP_DIR} has no world-readable files (outside venv)"
else
    warn "${APP_DIR} contains ${WORLD_APP} world-readable file(s) outside venv"
fi

# ── 6. Python virtual environment ────────────────────────────────────────────
hdr "Python Virtual Environment"
if [[ -f "${VENV}/bin/python" ]]; then
    ok "${VENV}/bin/python exists"
    PYVER="$("${VENV}/bin/python" --version 2>&1 || echo 'unknown')"
    if [[ "${PYVER}" =~ Python\ 3\.1[2-9] ]]; then
        ok "Python version: ${PYVER}"
    else
        warn "Python version: ${PYVER} (expected 3.12+)"
    fi
else
    fail "${VENV}/bin/python missing — venv not created"
fi

chk "gunicorn binary in venv" test -f "${VENV}/bin/gunicorn"

for pkg in Flask gunicorn APScheduler requests python-dotenv; do
    if "${VENV}/bin/pip" show "${pkg}" &>/dev/null; then
        VER="$("${VENV}/bin/pip" show "${pkg}" 2>/dev/null | grep '^Version:' | awk '{print $2}')"
        ok "pip: ${pkg} ${VER}"
    else
        fail "pip: ${pkg} not installed"
    fi
done

# ── 7. Systemd services ───────────────────────────────────────────────────────
hdr "Systemd Services"
for svc in "${WEB_SVC}" "${WORKER_SVC}"; do
    if [[ -f "/etc/systemd/system/${svc}.service" ]]; then
        ok "Unit file: /etc/systemd/system/${svc}.service"
    else
        fail "Unit file missing: /etc/systemd/system/${svc}.service"
    fi

    ACTIVE="$(systemctl is-active "${svc}" 2>/dev/null || echo 'unknown')"
    if [[ "${ACTIVE}" == "active" ]]; then
        ok "${svc}: active"
    else
        fail "${svc}: ${ACTIVE} (expected active)"
    fi

    ENABLED="$(systemctl is-enabled "${svc}" 2>/dev/null || echo 'unknown')"
    if [[ "${ENABLED}" == "enabled" ]]; then
        ok "${svc}: enabled"
    else
        fail "${svc}: ${ENABLED} (expected enabled)"
    fi
done

# Verify SCRIPT_NAME is set in the web unit file
if grep -q 'SCRIPT_NAME=/cklabScheduler' /etc/systemd/system/${WEB_SVC}.service 2>/dev/null; then
    ok "SCRIPT_NAME=/cklabScheduler in web unit"
else
    fail "SCRIPT_NAME=/cklabScheduler missing from web unit"
fi

# Verify EnvironmentFile path is correct
if grep -q "EnvironmentFile=${ENV_FILE}" /etc/systemd/system/${WEB_SVC}.service 2>/dev/null; then
    ok "EnvironmentFile=${ENV_FILE} in web unit"
else
    fail "EnvironmentFile path incorrect in web unit"
fi

# ── 8. Network port ───────────────────────────────────────────────────────────
hdr "Network Port"
if ss -tlnp 2>/dev/null | grep -q ':5080'; then
    ok "Port 5080 is listening"
    if ss -tlnp 2>/dev/null | grep ':5080' | grep -q '127.0.0.1'; then
        ok "Port 5080 bound to 127.0.0.1 (not 0.0.0.0)"
    else
        warn "Port 5080 listening but binding address unclear — verify Gunicorn --bind"
    fi
else
    fail "Port 5080 not listening — Gunicorn may not be running"
fi

# ── 9. Apache ─────────────────────────────────────────────────────────────────
hdr "Apache"
APACHE_ACTIVE="$(systemctl is-active apache2 2>/dev/null || echo 'unknown')"
if [[ "${APACHE_ACTIVE}" == "active" ]]; then
    ok "apache2 service: active"
else
    fail "apache2 service: ${APACHE_ACTIVE}"
fi

# Site enabled (symlink check — works without root)
if [[ -L /etc/apache2/sites-enabled/cklabscheduler.conf ]]; then
    ok "cklabscheduler site: enabled"
else
    fail "cklabscheduler site: not enabled in sites-enabled/"
fi

# Config file present
chk "Apache config file exists" test -f /etc/apache2/sites-available/cklabscheduler.conf

# Spot-check critical directives in the installed config
APACHE_CONF=/etc/apache2/sites-available/cklabscheduler.conf
if [[ -f "${APACHE_CONF}" ]]; then
    if grep -q 'ProxyPass .*/cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/' "${APACHE_CONF}" 2>/dev/null; then
        ok "ProxyPass /cklabScheduler/ → 127.0.0.1:5080/cklabScheduler/ (prefix preserved)"
    else
        fail "ProxyPass target must be http://127.0.0.1:5080/cklabScheduler/ (prefix preservation required for Gunicorn SCRIPT_NAME)"
    fi
    if grep -q 'ProxyPassReverse.*/cklabScheduler/' "${APACHE_CONF}" 2>/dev/null; then
        ok "ProxyPassReverse /cklabScheduler/ present"
    else
        fail "ProxyPassReverse missing from Apache config"
    fi
    if grep -qE 'RedirectMatch.*\^/cklabScheduler\$' "${APACHE_CONF}" 2>/dev/null; then
        ok "RedirectMatch anchored redirect present"
    else
        fail "RedirectMatch ^/cklabScheduler$ missing from Apache config"
    fi
    if grep -q 'ProxyPreserveHost On' "${APACHE_CONF}" 2>/dev/null; then
        ok "ProxyPreserveHost On present"
    else
        fail "ProxyPreserveHost On missing from Apache config"
    fi
fi

# Required modules (check mods-enabled symlinks — no root required)
for mod in proxy proxy_http ssl headers; do
    if [[ -f "/etc/apache2/mods-enabled/${mod}.load" || \
          -L "/etc/apache2/mods-enabled/${mod}.load" || \
          -f "/etc/apache2/mods-enabled/${mod}.conf" ]]; then
        ok "Apache module: ${mod}"
    else
        fail "Apache module not enabled: ${mod}"
    fi
done

# Apache config test (requires root on some systems)
if [[ "${EUID}" -eq 0 ]]; then
    if apache2ctl configtest &>/dev/null; then
        ok "apache2ctl configtest: PASS"
    else
        fail "apache2ctl configtest: FAIL — run 'apache2ctl configtest' for details"
    fi
else
    skip "apache2ctl configtest — requires root"
fi

# ── 10. Health endpoint ───────────────────────────────────────────────────────
hdr "Health Endpoint (${HEALTH_URL})"
HEALTH_JSON="$(curl --silent --insecure --max-time 15 "${HEALTH_URL}" 2>/dev/null || echo '{}')"
HTTP_CODE="$(curl --silent --insecure --max-time 15 --write-out '%{http_code}' --output /dev/null \
    "${HEALTH_URL}" 2>/dev/null || echo '000')"

if [[ "${HTTP_CODE}" == "200" ]]; then
    ok "HTTP 200 OK"
else
    fail "HTTP ${HTTP_CODE} (expected 200)"
fi

check_json_field() {
    local desc="$1" key="$2" expected="$3"
    local val
    val="$(printf '%s' "${HEALTH_JSON}" | \
        python3 -c "import sys,json; d=json.load(sys.stdin); print(str(d${key}).lower())" 2>/dev/null || echo 'error')"
    if [[ "${val}" == "${expected}" ]]; then
        ok "${desc}: ${val}"
    else
        fail "${desc}: got '${val}', expected '${expected}'"
    fi
}

check_json_field "ok"                       "['ok']"                      "true"
check_json_field "database.ok"              "['database']['ok']"          "true"
check_json_field "scheduler_worker.ok"      "['scheduler_worker']['ok']"  "true"
check_json_field "pexip.configured"         "['pexip']['configured']"     "true"

# Ensure no hostnames appear in the health response
CONF_HOSTNAME=""
if [[ -f "${ENV_FILE}" ]]; then
    CONF_HOSTNAME="$(grep '^COMMAND_HOST=' "${ENV_FILE}" 2>/dev/null \
        | sed 's/^COMMAND_HOST="\?\([^"]*\)"\?$/\1/' || true)"
fi
if [[ -n "${CONF_HOSTNAME}" ]]; then
    if printf '%s' "${HEALTH_JSON}" | grep -q "${CONF_HOSTNAME}"; then
        fail "Health response exposes COMMAND_HOST hostname"
    else
        ok "Health response does not expose COMMAND_HOST"
    fi
else
    skip "COMMAND_HOST not readable — cannot verify hostname exposure"
fi

# ── 11. SQLite database ───────────────────────────────────────────────────────
hdr "SQLite Database"
if [[ -f "${DB_PATH}" ]]; then
    ok "${DB_PATH} exists"
    DB_PERM="$(stat_perm "${DB_PATH}")"
    DB_OWNER="$(stat_owner "${DB_PATH}")"
    if [[ "${DB_OWNER}" == "${SVC_USER}:${SVC_USER}" ]]; then
        ok "${DB_PATH} owner: ${SVC_USER}:${SVC_USER}"
    else
        fail "${DB_PATH} owner: got ${DB_OWNER}, expected ${SVC_USER}:${SVC_USER}"
    fi
    if [[ "${DB_PERM}" =~ ^6[04][0-9]$ ]]; then
        ok "${DB_PATH} permissions: ${DB_PERM} (owner read/write)"
    else
        warn "${DB_PATH} permissions: ${DB_PERM} (expected 640 or similar)"
    fi

    # WAL mode
    WAL_MODE="$(sqlite3 "file:${DB_PATH}?mode=ro" "PRAGMA journal_mode;" 2>/dev/null || echo 'error')"
    if [[ "${WAL_MODE}" == "wal" ]]; then
        ok "WAL mode: enabled"
    else
        fail "WAL mode: ${WAL_MODE} (expected wal)"
    fi

    # Tables
    TABLES="$(sqlite3 "file:${DB_PATH}?mode=ro" ".tables" 2>/dev/null || echo '')"
    for tbl in meetings meeting_endpoints meeting_invitees scheduler_heartbeat; do
        if printf '%s' "${TABLES}" | grep -qw "${tbl}"; then
            ok "Table: ${tbl}"
        else
            fail "Table missing: ${tbl}"
        fi
    done

    # Meeting count
    MTG_COUNT="$(sqlite3 "file:${DB_PATH}?mode=ro" \
        "SELECT COUNT(*) FROM meetings;" 2>/dev/null || echo 'error')"
    if [[ "${MTG_COUNT}" =~ ^[0-9]+$ ]]; then
        ok "meetings table readable (${MTG_COUNT} row(s))"
    else
        fail "meetings table not readable"
    fi

    # Scheduler heartbeat
    HB_ROW="$(sqlite3 "file:${DB_PATH}?mode=ro" \
        "SELECT last_seen FROM scheduler_heartbeat WHERE id=1;" 2>/dev/null || echo '')"
    if [[ -n "${HB_ROW}" ]]; then
        EPOCH_LAST="$(date -d "${HB_ROW}" +%s 2>/dev/null || echo 0)"
        EPOCH_NOW="$(date +%s)"
        AGE=$(( EPOCH_NOW - EPOCH_LAST ))
        if [[ "${AGE}" -lt 30 ]]; then
            ok "Scheduler heartbeat: ${AGE}s ago (within 30s threshold)"
        elif [[ "${AGE}" -lt 60 ]]; then
            warn "Scheduler heartbeat: ${AGE}s ago (slightly stale — worker may be slow)"
        else
            fail "Scheduler heartbeat: ${AGE}s ago (stale — worker not ticking)"
        fi
    else
        fail "Scheduler heartbeat: no row in scheduler_heartbeat (worker has never written)"
    fi
else
    fail "${DB_PATH} does not exist"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
TOTAL=$(( PASS + FAIL + WARN ))
printf '\n\033[1m══════════════════════════════════════════════════════════\033[0m\n'
printf '\033[1m  Results: %d passed, %d failed, %d warnings  (%d checks)\033[0m\n' \
    "${PASS}" "${FAIL}" "${WARN}" "${TOTAL}"
printf '\033[1m══════════════════════════════════════════════════════════\033[0m\n\n'

if [[ "${FAIL}" -gt 0 ]]; then
    printf 'FAIL — %d check(s) failed. Review output above.\n' "${FAIL}" >&2
    exit 1
elif [[ "${WARN}" -gt 0 ]]; then
    printf 'PASS with warnings — review the ⚠ items above.\n'
    exit 0
else
    printf 'ALL CHECKS PASSED\n'
    exit 0
fi
