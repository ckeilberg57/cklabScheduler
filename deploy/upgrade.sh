#!/usr/bin/env bash
# deploy/upgrade.sh — Non-interactive upgrade script for cklabScheduler
# Stops both services, replaces application code and dependencies, runs schema
# migrations, then restarts.  Configuration and the database are preserved.
set -euo pipefail

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Helpers ──────────────────────────────────────────────────────────────────
die()  { echo; echo "FATAL: $*" >&2; exit 1; }
info() { echo; printf '══ %s ══\n' "$*"; }

# ── 1. Verify root ───────────────────────────────────────────────────────────
info "Pre-flight"
[[ "${EUID}" -eq 0 ]] || die "This script must be run as root."

# ── 2. Verify existing install ───────────────────────────────────────────────
[[ -d "${VENV}" ]]   || die "Virtual environment not found at ${VENV}. Run install.sh first."
[[ -f "${ENV_FILE}" ]] || die "Configuration not found at ${ENV_FILE}. Run install.sh first."
echo "  Existing install confirmed."

# ── 3. Stop both services ────────────────────────────────────────────────────
info "Stopping services"
# Any meetings in 'starting' or 'ending' will be recovered on worker restart
# by recover_stuck_meetings() if their window has not passed.
systemctl stop "${WEB_SVC}"    || echo "  ${WEB_SVC} was not running."
systemctl stop "${WORKER_SVC}" || echo "  ${WORKER_SVC} was not running."
echo "  Both services stopped."

# ── 4. Back up database ───────────────────────────────────────────────────────
info "Backing up database"
if [[ -f "${DB_PATH}" ]]; then
    TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
    BACKUP="${DB_PATH}.bak.${TIMESTAMP}"
    cp "${DB_PATH}" "${BACKUP}"
    echo "  Backup: ${BACKUP}"
else
    echo "  No database found at ${DB_PATH} — skipping backup."
fi

# ── 5. Replace application files ─────────────────────────────────────────────
info "Replacing application files"
rsync -a --delete \
    --exclude='.git' \
    --exclude='.git/' \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.db' \
    --exclude='.env' \
    --exclude='.env.original' \
    --exclude='.env.local' \
    --exclude='.DS_Store' \
    --exclude='deploy/' \
    --exclude='tests/' \
    --exclude='REBUILD_PLAN.md' \
    --exclude='.pytest_cache/' \
    --exclude='*.egg-info' \
    "${REPO_ROOT}/" "${APP_DIR}/"

chown -R "root:${SVC_USER}" "${APP_DIR}"
find "${APP_DIR}" -not -path "${VENV}" -not -path "${VENV}/*" \
    -type d -exec chmod 750 {} +
find "${APP_DIR}" -not -path "${VENV}" -not -path "${VENV}/*" \
    -type f -exec chmod 640 {} +
echo "  Files replaced. Ownership: root:${SVC_USER}; dirs 750, files 640."

# ── 6. Update Python dependencies ────────────────────────────────────────────
info "Updating Python dependencies"
echo "  Upgrading pip..."
"${VENV}/bin/pip" install --upgrade --no-input pip
echo "  Updating application dependencies..."
"${VENV}/bin/pip" install --upgrade --no-input -r "${APP_DIR}/requirements.txt"
echo "  Dependencies updated."

# ── 7. Run database migrations ────────────────────────────────────────────────
info "Running database migrations"
(
    cd "${APP_DIR}"
    DB_PATH="${DB_PATH}" "${VENV}/bin/python" \
        -c "from app.database import init_db; init_db()"
)
chown "${SVC_USER}:${SVC_USER}" "${DB_PATH}" 2>/dev/null || true
chmod 640 "${DB_PATH}" 2>/dev/null || true
echo "  Schema up to date."

# ── 8. Reload systemd (unit files may have changed) ───────────────────────────
info "Updating systemd unit files"
cp "${SCRIPT_DIR}/cklab-scheduler-web.service"    /etc/systemd/system/
cp "${SCRIPT_DIR}/cklab-scheduler-worker.service" /etc/systemd/system/
systemctl daemon-reload
echo "  Unit files updated and daemon reloaded."

# ── 9. Apache configuration migration (r2 → r3 ProxyPass fix) ────────────────
info "Updating Apache configuration"
APACHE_CONF="/etc/apache2/sites-available/cklabscheduler.conf"
#
# Detection:
#   r3 (correct): ProxyPass /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/
#   r2 (broken):  ProxyPass /cklabScheduler/ http://127.0.0.1:5080/   ← no prefix
#
if [[ ! -f "${APACHE_CONF}" ]]; then
    echo "  No config at ${APACHE_CONF} — skipping."
elif grep -qE 'ProxyPass[[:space:]]*/cklabScheduler/[[:space:]]+http://127\.0\.0\.1:5080/cklabScheduler/' "${APACHE_CONF}"; then
    echo "  ProxyPass already uses r3 prefix-preserved format — no change needed."
elif grep -qE 'ProxyPass[[:space:]]*/cklabScheduler/[[:space:]]+http://127\.0\.0\.1:5080/[[:space:]]*$' "${APACHE_CONF}"; then
    # r2 broken ProxyPass detected: target is http://127.0.0.1:5080/ with no path prefix.
    # Gunicorn receives /api/health instead of /cklabScheduler/api/health and cannot
    # split on SCRIPT_NAME, producing IndexError before Flask is ever reached.
    # Fix: surgical sed on lines containing /cklabScheduler/ only.
    echo "  Detected r2 ProxyPass (http://127.0.0.1:5080/) — applying r3 migration..."
    APACHE_CONF_BAK="${APACHE_CONF}.bak.$(date +%Y%m%dT%H%M%S)"
    cp "${APACHE_CONF}" "${APACHE_CONF_BAK}"
    echo "  Backup: ${APACHE_CONF_BAK}"
    sed -i \
        '/\/cklabScheduler\// s|http://127\.0\.0\.1:5080/[[:space:]]*$|http://127.0.0.1:5080/cklabScheduler/|' \
        "${APACHE_CONF}"
    echo "  ProxyPass lines updated. Validating new configuration..."
    if apache2ctl configtest; then
        systemctl reload apache2
        echo "  Apache configuration validated and reloaded."
    else
        echo "  ERROR: apache2ctl configtest failed — restoring backup."
        cp "${APACHE_CONF_BAK}" "${APACHE_CONF}"
        die "Apache configuration migration failed. Restored from ${APACHE_CONF_BAK}. Review output above."
    fi
else
    echo "  WARNING: ProxyPass pattern not recognized (possibly a custom configuration)."
    echo "  No changes made to ${APACHE_CONF}."
    echo
    echo "  To apply the r3 prefix-preservation fix manually, update ${APACHE_CONF}:"
    echo "    ProxyPass        /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/"
    echo "    ProxyPassReverse /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/"
    echo "  Then reload: apache2ctl configtest && systemctl reload apache2"
fi

# ── 10. Migrate environment configuration ────────────────────────────────────
info "Updating environment configuration"
# Add new configuration keys with safe defaults when not already present.
# Never overwrite a value that was set by the administrator.
_add_env_default() {
    local key="$1" default="$2"
    if grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
        echo "  ${key} already set — preserving existing value."
    else
        printf '%s="%s"\n' "${key}" "${default}" >> "${ENV_FILE}"
        echo "  ${key} not found — added default: ${default}"
    fi
}
_add_env_default "APP_DISPLAY_NAME" "CKlabs Scheduler"

# ── 11. Start both services ───────────────────────────────────────────────────
info "Starting services"
systemctl start "${WEB_SVC}"
systemctl start "${WORKER_SVC}"
echo "  Both services started."

# ── 12. Health check ──────────────────────────────────────────────────────────
info "Health check"
echo "  Waiting for services to initialise..."
sleep 5

HEALTH_JSON="$(curl --silent --insecure --max-time 15 \
    "https://localhost/cklabScheduler/api/health" || echo '{}')"

if printf '%s' "${HEALTH_JSON}" | grep -q '"ok": *true'; then
    echo
    echo "  ✓ Upgrade complete."
    echo
    echo "  Log commands:"
    echo "    journalctl -u ${WEB_SVC}    -f"
    echo "    journalctl -u ${WORKER_SVC} -f"
    echo
else
    echo
    echo "  WARNING: Health check did not return ok=true."
    echo "  Response:"
    printf '%s\n' "${HEALTH_JSON}" | python3 -m json.tool 2>/dev/null \
        || printf '%s\n' "${HEALTH_JSON}"
    echo
    if [[ -n "${BACKUP:-}" ]]; then
        echo "  Database rollback (if schema migration caused issues):"
        echo "    systemctl stop ${WEB_SVC} ${WORKER_SVC}"
        echo "    cp ${BACKUP} ${DB_PATH}"
        echo "    systemctl start ${WEB_SVC} ${WORKER_SVC}"
    fi
    echo
    echo "  Logs:"
    echo "    journalctl -u ${WEB_SVC}    --no-pager -n 50"
    echo "    journalctl -u ${WORKER_SVC} --no-pager -n 50"
    exit 1
fi
