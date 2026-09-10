#!/usr/bin/env bash
# deploy/upgrade.sh — Non-interactive upgrade script for SBALKC Scheduler
# Stops both services, replaces application code and dependencies, runs schema
# migrations, then restarts.  Configuration and the database are preserved.
set -euo pipefail

# ── Constants ────────────────────────────────────────────────────────────────
APP_DIR="/opt/sbalkcScheduler"
CONF_DIR="/etc/sbalkcScheduler"
DATA_DIR="/var/lib/sbalkcScheduler"
ENV_FILE="${CONF_DIR}/sbalkcScheduler.env"
DB_PATH="${DATA_DIR}/scheduler.db"
VENV="${APP_DIR}/venv"
SVC_USER="sbalkcscheduler"
WEB_SVC="sbalkc-scheduler-web"
WORKER_SVC="sbalkc-scheduler-worker"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Helpers ──────────────────────────────────────────────────────────────────
die()  { echo; echo "FATAL: $*" >&2; exit 1; }
info() { echo; printf '══ %s ══\n' "$*"; }

# ── 1. Verify root ───────────────────────────────────────────────────────────
info "Pre-flight"
[[ "${EUID}" -eq 0 ]] || die "This script must be run as root."

# ── 2. Detect and refuse old CKLab installation ──────────────────────────────
# The r10 release renamed all runtime identifiers from CKLab to SBALKC.
# upgrade.sh cannot safely migrate an existing CKLab installation in-place
# because the runtime paths, service account, and service unit names all
# changed.  Attempting an upgrade over the old layout would leave a broken
# hybrid installation.
#
# Policy (Option A): detect the old layout and refuse with clear instructions.
if [[ -d "/opt/cklabScheduler" ]]; then
    echo
    echo "  ERROR: Found a CKLab Scheduler installation at /opt/cklabScheduler."
    echo
    echo "  This version of upgrade.sh manages the SBALKC Scheduler layout:"
    echo "    /opt/sbalkcScheduler/       (application)"
    echo "    /etc/sbalkcScheduler/       (configuration)"
    echo "    /var/lib/sbalkcScheduler/   (database)"
    echo "    sbalkc-scheduler-web        (systemd service)"
    echo "    sbalkc-scheduler-worker     (systemd service)"
    echo "    sbalkcscheduler             (service account)"
    echo
    echo "  Upgrade from CKLab to SBALKC requires a fresh installation:"
    echo
    echo "    1. Back up your database:"
    echo "         cp /var/lib/cklabScheduler/scheduler.db /root/scheduler.db.bak"
    echo
    echo "    2. Back up your configuration:"
    echo "         cp /etc/cklabScheduler/cklabScheduler.env /root/cklabScheduler.env.bak"
    echo
    echo "    3. Run the uninstaller to remove the old CKLab installation:"
    echo "         sudo bash deploy/uninstall.sh"
    echo "         (choose Y to remove code, config, and data only if you have backups)"
    echo
    echo "    4. Run the installer for the new SBALKC layout:"
    echo "         sudo bash deploy/install.sh"
    echo "         (restore your database when prompted)"
    echo
    die "CKLab installation detected — cannot upgrade in-place. See instructions above."
fi

# ── 3. Verify existing SBALKC install ────────────────────────────────────────
[[ -d "${VENV}" ]]   || die "Virtual environment not found at ${VENV}. Run install.sh first."
[[ -f "${ENV_FILE}" ]] || die "Configuration not found at ${ENV_FILE}. Run install.sh first."
echo "  Existing SBALKC install confirmed."

# ── 4. Stop both services ────────────────────────────────────────────────────
info "Stopping services"
# Any meetings in 'starting' or 'ending' will be recovered on worker restart
# by recover_stuck_meetings() if their window has not passed.
systemctl stop "${WEB_SVC}"    || echo "  ${WEB_SVC} was not running."
systemctl stop "${WORKER_SVC}" || echo "  ${WORKER_SVC} was not running."
echo "  Both services stopped."

# ── 5. Back up database ───────────────────────────────────────────────────────
info "Backing up database"
if [[ -f "${DB_PATH}" ]]; then
    TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
    BACKUP="${DB_PATH}.bak.${TIMESTAMP}"
    cp "${DB_PATH}" "${BACKUP}"
    echo "  Backup: ${BACKUP}"
else
    echo "  No database found at ${DB_PATH} — skipping backup."
fi

# ── 6. Replace application files ─────────────────────────────────────────────
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

# ── 7. Update Python dependencies ────────────────────────────────────────────
info "Updating Python dependencies"
echo "  Upgrading pip..."
"${VENV}/bin/pip" install --upgrade --no-input pip
echo "  Updating application dependencies..."
"${VENV}/bin/pip" install --upgrade --no-input -r "${APP_DIR}/requirements.txt"
echo "  Dependencies updated."

# ── 8. Run database migrations ────────────────────────────────────────────────
info "Running database migrations"
(
    cd "${APP_DIR}"
    DB_PATH="${DB_PATH}" "${VENV}/bin/python" \
        -c "from app.database import init_db; init_db()"
)
chown "${SVC_USER}:${SVC_USER}" "${DB_PATH}" 2>/dev/null || true
chmod 640 "${DB_PATH}" 2>/dev/null || true
echo "  Schema up to date."

# ── 9. Reload systemd (unit files may have changed) ───────────────────────────
info "Updating systemd unit files"
cp "${SCRIPT_DIR}/sbalkc-scheduler-web.service"    /etc/systemd/system/
cp "${SCRIPT_DIR}/sbalkc-scheduler-worker.service" /etc/systemd/system/
systemctl daemon-reload
echo "  Unit files updated and daemon reloaded."

# ── 10. Apache configuration migration (r2 → r3 ProxyPass fix) ────────────────
info "Updating Apache configuration"
APACHE_CONF="/etc/apache2/sites-available/sbalkcscheduler.conf"
#
# Detection:
#   r3 (correct): ProxyPass /sbalkcScheduler/ http://127.0.0.1:5080/sbalkcScheduler/
#   r2 (broken):  ProxyPass /sbalkcScheduler/ http://127.0.0.1:5080/   ← no prefix
#
if [[ ! -f "${APACHE_CONF}" ]]; then
    echo "  No config at ${APACHE_CONF} — skipping."
elif grep -qE 'ProxyPass[[:space:]]*/sbalkcScheduler/[[:space:]]+http://127\.0\.0\.1:5080/sbalkcScheduler/' "${APACHE_CONF}"; then
    echo "  ProxyPass already uses r3 prefix-preserved format — no change needed."
elif grep -qE 'ProxyPass[[:space:]]*/sbalkcScheduler/[[:space:]]+http://127\.0\.0\.1:5080/[[:space:]]*$' "${APACHE_CONF}"; then
    # r2 broken ProxyPass detected: target is http://127.0.0.1:5080/ with no path prefix.
    # Gunicorn receives /api/health instead of /sbalkcScheduler/api/health and cannot
    # split on SCRIPT_NAME, producing IndexError before Flask is ever reached.
    # Fix: surgical sed on lines containing /sbalkcScheduler/ only.
    echo "  Detected r2 ProxyPass (http://127.0.0.1:5080/) — applying r3 migration..."
    APACHE_CONF_BAK="${APACHE_CONF}.bak.$(date +%Y%m%dT%H%M%S)"
    cp "${APACHE_CONF}" "${APACHE_CONF_BAK}"
    echo "  Backup: ${APACHE_CONF_BAK}"
    sed -i \
        '/\/sbalkcScheduler\// s|http://127\.0\.0\.1:5080/[[:space:]]*$|http://127.0.0.1:5080/sbalkcScheduler/|' \
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
    echo "    ProxyPass        /sbalkcScheduler/ http://127.0.0.1:5080/sbalkcScheduler/"
    echo "    ProxyPassReverse /sbalkcScheduler/ http://127.0.0.1:5080/sbalkcScheduler/"
    echo "  Then reload: apache2ctl configtest && systemctl reload apache2"
fi

# ── 11. Migrate environment configuration ────────────────────────────────────
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
_add_env_default "APP_DISPLAY_NAME"    "SBALKC Scheduler"
_add_env_default "LOCAL_AUTH_ENABLED"  "true"
_add_env_default "ENTRA_ENABLED"       "false"
_add_env_default "SESSION_COOKIE_SECURE" "true"

# If this is the first upgrade that adds authentication, remind the operator
# to create a local admin user before trying to log in.
if ! grep -q "^LOCAL_AUTH_ENABLED=" "${ENV_FILE}.bak."* 2>/dev/null; then
    echo
    echo "  NOTE: Authentication has been added in this release."
    echo "  If you have not already created an admin user, run:"
    echo "    sudo ${APP_DIR}/venv/bin/python -m app.manage_users create \\"
    echo "         --username <admin> --role administrator"
    echo "  (you will be prompted for the password)"
fi

# ── 12. Start both services ───────────────────────────────────────────────────
info "Starting services"
systemctl start "${WEB_SVC}"
systemctl start "${WORKER_SVC}"
echo "  Both services started."

# ── 13. Health check ──────────────────────────────────────────────────────────
info "Health check"
echo "  Waiting for services to initialise..."
sleep 5

HEALTH_JSON="$(curl --silent --insecure --max-time 15 \
    "https://localhost/sbalkcScheduler/api/health" || echo '{}')"

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
