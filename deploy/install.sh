#!/usr/bin/env bash
# deploy/install.sh — Interactive installer for cklabScheduler on Ubuntu 24.04
# Run as root from within the repository directory or any path; the script
# locates the repo root relative to its own location.
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
CERT_FILE="/etc/ssl/certs/cklabscheduler.crt"
KEY_FILE="/etc/ssl/private/cklabscheduler.key"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Progress tracking ─────────────────────────────────────────────────────────
_STEP=0
_TOTAL=17
STAGE="startup"

trap 'printf "\n\033[31mFATAL:\033[0m Installation failed during: %s (line %d)\nRerun the installer to retry.\n" "${STAGE}" "${BASH_LINENO[0]}" >&2' ERR

# ── Helpers ──────────────────────────────────────────────────────────────────
die()  { printf '\n\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
info() {
    _STEP=$((_STEP + 1))
    STAGE="$*"
    printf '\n\033[1m[%d/%d] %s\033[0m\n' "${_STEP}" "${_TOTAL}" "${STAGE}"
}

# All prompt helpers read from /dev/tty so they work even if stdin is a pipe.
# They write the collected value to stdout; callers capture with $(...).
# Prompt text goes to /dev/tty (never captured).

_tty_print() { printf '%s' "$*" > /dev/tty 2>/dev/null || true; }

prompt_required() {
    local prompt="$1" reply
    while true; do
        _tty_print "  ${prompt}: "
        read -r reply < /dev/tty \
            || die "Cannot read from terminal (/dev/tty). Run the installer interactively."
        [[ -n "${reply}" ]] && { printf '%s' "${reply}"; return 0; }
        echo "  (required — please enter a value)" > /dev/tty 2>/dev/null || true
    done
}

prompt_default() {
    local prompt="$1" default="$2" reply
    _tty_print "  ${prompt} [${default}]: "
    read -r reply < /dev/tty \
        || die "Cannot read from terminal (/dev/tty). Run the installer interactively."
    printf '%s' "${reply:-${default}}"
}

prompt_secret() {
    # Required secret — not echoed
    local prompt="$1" reply
    while true; do
        _tty_print "  ${prompt}: "
        read -rs reply < /dev/tty \
            || die "Cannot read from terminal (/dev/tty). Run the installer interactively."
        printf '\n' > /dev/tty 2>/dev/null || true
        [[ -n "${reply}" ]] && { printf '%s' "${reply}"; return 0; }
        echo "  (required — please enter a value)" > /dev/tty 2>/dev/null || true
    done
}

prompt_secret_optional() {
    # Optional secret — not echoed; empty string accepted
    local prompt="$1" reply
    _tty_print "  ${prompt} (press Enter to skip): "
    read -rs reply < /dev/tty || true
    printf '\n' > /dev/tty 2>/dev/null || true
    printf '%s' "${reply}"
}

prompt_optional() {
    local prompt="$1" reply
    _tty_print "  ${prompt} (press Enter to skip): "
    read -r reply < /dev/tty || true
    printf '%s' "${reply}"
}

prompt_yesno() {
    # Returns 0 for yes, 1 for no.  Use only in 'if' or '&&' contexts.
    local prompt="$1" default="${2:-N}" reply
    _tty_print "  ${prompt} [${default}]: "
    read -r reply < /dev/tty || true
    reply="${reply:-${default}}"
    [[ "${reply,,}" =~ ^y(es)?$ ]]
}

write_env_line() {
    # Emit KEY="value" with backslashes and double-quotes escaped.
    # Writes to the caller's stdout (redirect the whole block to ENV_FILE).
    local key="$1" val="$2"
    val="${val//\\/\\\\}"
    val="${val//\"/\\\"}"
    printf '%s="%s"\n' "${key}" "${val}"
}

# ── Multiplexer recommendation ────────────────────────────────────────────────
if [[ -z "${STY:-}" && -z "${TMUX:-}" ]]; then
    printf '\n\033[33mTIP:\033[0m This installation takes several minutes and will prompt for\n'
    printf '    Pexip credentials.  To protect against SSH disconnects, consider\n'
    printf '    running inside screen or tmux:\n'
    printf '      screen -S cklabinstall   (then re-run this script inside screen)\n\n'
fi

# ── 1. Pre-flight ────────────────────────────────────────────────────────────
info "Pre-flight checks"

[[ "${EUID}" -eq 0 ]] || die "This script must be run as root."

if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "24.04" ]] \
        || die "Ubuntu 24.04 required. Detected: ${PRETTY_NAME:-unknown}."
else
    die "/etc/os-release not found. Ubuntu 24.04 required."
fi

[[ -c /dev/tty ]] \
    || die "No controlling terminal (/dev/tty). Interactive prompts require a TTY. Use screen or tmux for SSH sessions."

echo "  Checking internet connectivity..."
curl --silent --max-time 10 --head https://pypi.org > /dev/null \
    || die "No internet access. Required to install Python dependencies."

echo "  Pre-flight checks passed."

# ── 2. System packages ───────────────────────────────────────────────────────
info "Installing system packages"
echo "  Running apt-get update..."
apt-get update -qq
echo "  Installing packages: python3.12, python3.12-venv, tzdata, sqlite3, curl, apache2, openssl, rsync"
apt-get install -y python3.12 python3.12-venv tzdata sqlite3 curl apache2 openssl rsync
echo "  Packages installed."

# ── 3. Apache modules ────────────────────────────────────────────────────────
info "Enabling Apache modules"
a2enmod proxy proxy_http ssl headers
echo "  Modules enabled: proxy proxy_http ssl headers"

# ── 4. Service account ───────────────────────────────────────────────────────
info "Service account"
if id "${SVC_USER}" &>/dev/null; then
    echo "  Account '${SVC_USER}' already exists — skipping creation."
    # Ensure home field is /nonexistent (fix for accounts created without --home-dir flag)
    CURRENT_HOME="$(getent passwd "${SVC_USER}" | cut -d: -f6)"
    if [[ "${CURRENT_HOME}" != "/nonexistent" ]]; then
        usermod --home /nonexistent "${SVC_USER}"
        echo "  Corrected home directory field to /nonexistent."
    fi
else
    useradd --system --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin "${SVC_USER}"
    echo "  Account '${SVC_USER}' created (home: /nonexistent, shell: /usr/sbin/nologin)."
fi

# ── 5. Directories ───────────────────────────────────────────────────────────
info "Creating directories"
install -d -m 750 -o root          -g "${SVC_USER}" "${APP_DIR}"
install -d -m 750 -o root          -g "${SVC_USER}" "${CONF_DIR}"
install -d -m 750 -o "${SVC_USER}" -g "${SVC_USER}" "${DATA_DIR}"
echo "  ${APP_DIR}  (root:${SVC_USER} 750)"
echo "  ${CONF_DIR} (root:${SVC_USER} 750)"
echo "  ${DATA_DIR} (${SVC_USER}:${SVC_USER} 750)"

# ── 6. Application files ─────────────────────────────────────────────────────
info "Copying application files"
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

# Set ownership; venv will be created next and needs its own permissions
chown -R "root:${SVC_USER}" "${APP_DIR}"
find "${APP_DIR}" -not -path "${VENV}" -not -path "${VENV}/*" \
    -type d -exec chmod 750 {} +
find "${APP_DIR}" -not -path "${VENV}" -not -path "${VENV}/*" \
    -type f -exec chmod 640 {} +
echo "  Files copied. Ownership: root:${SVC_USER}; dirs 750, files 640."

# ── 7. Python virtual environment ────────────────────────────────────────────
info "Python virtual environment"
if [[ ! -d "${VENV}" ]]; then
    echo "  Creating venv at ${VENV}..."
    python3.12 -m venv "${VENV}"
    echo "  Venv created."
else
    echo "  Existing venv found — reusing."
fi
echo "  Upgrading pip (this may take a moment)..."
"${VENV}/bin/pip" install --upgrade --no-input pip
echo "  Installing application dependencies (this may take 1-2 minutes)..."
"${VENV}/bin/pip" install --no-input -r "${APP_DIR}/requirements.txt"
echo "  Dependencies installed."

# ── 8. Database migration ────────────────────────────────────────────────────
info "Database migration (optional)"
if prompt_yesno "Migrate an existing scheduler.db?" "N"; then
    while true; do
        EXISTING_DB="$(prompt_required "Path to existing scheduler.db")"
        if [[ ! -f "${EXISTING_DB}" ]]; then
            echo "  File not found: ${EXISTING_DB}" > /dev/tty 2>/dev/null || true
            continue
        fi
        if sqlite3 "${EXISTING_DB}" "SELECT COUNT(*) FROM meetings;" &>/dev/null; then
            break
        fi
        echo "  Not a valid scheduler.db (missing 'meetings' table)." > /dev/tty 2>/dev/null || true
    done
    TIMESTAMP="$(date +%Y%m%dT%H%M%S)"
    BACKUP="${EXISTING_DB}.pre-install-${TIMESTAMP}.bak"
    cp "${EXISTING_DB}" "${BACKUP}"
    echo "  Backup: ${BACKUP}"
    cp "${EXISTING_DB}" "${DB_PATH}"
    chown "${SVC_USER}:${SVC_USER}" "${DB_PATH}"
    chmod 640 "${DB_PATH}"
    echo "  Database copied to ${DB_PATH}."
else
    echo "  Starting fresh — no migration."
fi

# ── 9. Interactive configuration ─────────────────────────────────────────────
info "Configuration (interactive)"
echo "  Values marked [hidden] are not echoed to the terminal."
echo

echo "── Pexip ──────────────────────────────────────────────────────────────"
REG_STATUS_HOST="$(prompt_required "Pexip Registration/Status host (e.g. pexip-mgr.example.com)")"
COMMAND_HOST="$(prompt_required    "Pexip Command/Edge host     (e.g. pexip-edge.example.com)")"
MGMT_USER="$(prompt_required       "Pexip Management API username")"
MGMT_PASS="$(prompt_secret         "Pexip Management API password [hidden]")"
VERIFY_TLS="$(prompt_default       "Verify TLS certificates (true/false)" "true")"
HOST_PIN="$(prompt_secret_optional "Conference host PIN [hidden]")"
CONTROL_DISPLAY_NAME="$(prompt_default "Scheduler display name in Pexip" "Scheduler")"
DIAL_PROTOCOL="$(prompt_default       "Dial protocol (auto/sip/h323/rtmp/mssip)" "auto")"
echo
_tty_print "  WebRTC base URL (press Enter to use https://${COMMAND_HOST}/webapp3/m/): "
read -r WEBRTC_BASE_URL < /dev/tty || true
WEBRTC_BASE_URL="${WEBRTC_BASE_URL:-https://${COMMAND_HOST}/webapp3/m/}"

echo
echo "── Scheduler ──────────────────────────────────────────────────────────"
APP_DISPLAY_NAME="$(prompt_default "Application display name (shown in UI title and branding)" "CKlabs Scheduler")"
ABOUT_TO_START_MINUTES="$(prompt_default "Minutes before start to show 'about to start'" "1")"
DEFAULT_EXTEND_MINUTES="$(prompt_default "Default extension duration in minutes" "15")"
POLL_SECONDS="$(prompt_default           "Frontend poll interval in seconds" "3")"

echo
echo "── Microsoft 365 (optional) ───────────────────────────────────────────"
O365_ENABLED_VAL="false"
O365_TENANT_ID=""
O365_CLIENT_ID=""
O365_CLIENT_SECRET=""
O365_FROM_MAILBOX=""
O365_EMAIL_SUBJECT="Your Secure Virtual Consultation"
O365_ORGANIZER_NAME="Pexip Scheduler"
O365_TIMEZONE="America/New_York"
O365_LOCATION="Secure Virtual Session"
O365_INCLUDE_ICS_VAL="true"
O365_ALLOW_PROPOSE_NEW_TIME_VAL="false"
O365_SAVE_TO_SENT_ITEMS_VAL="true"

if prompt_yesno "Enable Microsoft 365 email integration?" "N"; then
    O365_ENABLED_VAL="true"
    O365_TENANT_ID="$(prompt_secret   "O365 Tenant ID [hidden]")"
    O365_CLIENT_ID="$(prompt_required  "O365 Application (Client) ID")"
    O365_CLIENT_SECRET="$(prompt_secret "O365 Client Secret [hidden]")"
    O365_FROM_MAILBOX="$(prompt_required "O365 From mailbox address")"
    O365_EMAIL_SUBJECT="$(prompt_default  "Email subject" "Your Secure Virtual Consultation")"
    O365_ORGANIZER_NAME="$(prompt_default "Organizer display name" "Pexip Scheduler")"
    O365_TIMEZONE="$(prompt_default       "ICS timezone (IANA name)" "America/New_York")"
    O365_LOCATION="$(prompt_default       "Meeting location string" "Secure Virtual Session")"
    if prompt_yesno "Include ICS attachment?" "Y"; then
        O365_INCLUDE_ICS_VAL="true"
    else
        O365_INCLUDE_ICS_VAL="false"
    fi
    if prompt_yesno "Allow attendees to propose a new time?" "N"; then
        O365_ALLOW_PROPOSE_NEW_TIME_VAL="true"
    else
        O365_ALLOW_PROPOSE_NEW_TIME_VAL="false"
    fi
    if prompt_yesno "Save sent emails to Sent Items?" "Y"; then
        O365_SAVE_TO_SENT_ITEMS_VAL="true"
    else
        O365_SAVE_TO_SENT_ITEMS_VAL="false"
    fi
fi

echo
echo "── Apache / TLS ───────────────────────────────────────────────────────"
SERVER_HOSTNAME="$(prompt_required "Server hostname (used as Apache ServerName)")"
echo
echo "  TLS options:"
echo "    1) Provide paths to an existing certificate and key"
echo "    2) Generate a self-signed certificate (lab/internal use only)"
TLS_OPTION=""
while [[ "${TLS_OPTION}" != "1" && "${TLS_OPTION}" != "2" ]]; do
    _tty_print "  Select [1/2]: "
    read -r TLS_OPTION < /dev/tty \
        || die "Cannot read from terminal (/dev/tty). Run the installer interactively."
done

if [[ "${TLS_OPTION}" == "1" ]]; then
    while true; do
        USER_CERT="$(prompt_required "Path to certificate file (.crt or .pem)")"
        USER_KEY="$(prompt_required  "Path to private key file")"
        if [[ -f "${USER_CERT}" && -f "${USER_KEY}" ]]; then
            break
        fi
        echo "  One or both paths not found — please check and try again." > /dev/tty 2>/dev/null || true
    done
    cp "${USER_CERT}" "${CERT_FILE}"
    cp "${USER_KEY}"  "${KEY_FILE}"
    chmod 644 "${CERT_FILE}"
    chmod 600 "${KEY_FILE}"
    echo "  Certificate installed from provided paths."
else
    echo "  Generating self-signed certificate (4096-bit RSA, 10 years)..."
    openssl req -x509 -newkey rsa:4096 -days 3650 -nodes \
        -subj "/CN=${SERVER_HOSTNAME}" \
        -keyout "${KEY_FILE}" \
        -out    "${CERT_FILE}" \
        2>/dev/null
    chmod 644 "${CERT_FILE}"
    chmod 600 "${KEY_FILE}"
    echo "  Self-signed certificate generated."
fi

echo
echo "── Authentication ─────────────────────────────────────────────────────"
echo "  Local admin accounts are enabled by default."
echo "  At least one authentication method must remain enabled."
echo
LOCAL_AUTH_ENABLED_VAL="true"
ENTRA_ENABLED_VAL="false"
ADMIN_USER=""
ADMIN_PASS=""
ENTRA_TENANT_ID_VAL=""
ENTRA_CLIENT_ID_VAL=""
ENTRA_CLIENT_SECRET_VAL=""
ENTRA_REDIRECT_URI_VAL=""
ENTRA_POST_LOGOUT_REDIRECT_URI_VAL=""

echo "  Initial local admin account"
ADMIN_USER="$(prompt_required "Admin username")"
while true; do
    ADMIN_PASS="$(prompt_secret "Admin password (min 12 chars) [hidden]")"
    ADMIN_PASS_CONFIRM="$(prompt_secret "Confirm admin password [hidden]")"
    if [[ "${ADMIN_PASS}" != "${ADMIN_PASS_CONFIRM}" ]]; then
        echo "  Passwords do not match — please try again." > /dev/tty 2>/dev/null || true
        continue
    fi
    if [[ "${#ADMIN_PASS}" -lt 12 ]]; then
        echo "  Password is too short (minimum 12 characters) — please try again." > /dev/tty 2>/dev/null || true
        continue
    fi
    break
done
unset ADMIN_PASS_CONFIRM

echo
if prompt_yesno "Enable Microsoft Entra ID (Azure AD) authentication?" "N"; then
    ENTRA_ENABLED_VAL="true"
    ENTRA_TENANT_ID_VAL="$(prompt_required "Entra Tenant ID (from Azure portal)")"
    ENTRA_CLIENT_ID_VAL="$(prompt_required  "Entra Application (Client) ID")"
    ENTRA_CLIENT_SECRET_VAL="$(prompt_secret "Entra Client Secret [hidden]")"
    _ENTRA_DEFAULT_REDIRECT="https://${SERVER_HOSTNAME}/cklabScheduler/auth/callback"
    ENTRA_REDIRECT_URI_VAL="$(prompt_default "Entra redirect URI" "${_ENTRA_DEFAULT_REDIRECT}")"
    _ENTRA_DEFAULT_POST_LOGOUT="https://${SERVER_HOSTNAME}/cklabScheduler/login"
    ENTRA_POST_LOGOUT_REDIRECT_URI_VAL="$(prompt_default "Post-logout redirect URI" "${_ENTRA_DEFAULT_POST_LOGOUT}")"

    echo
    if prompt_yesno "Keep local admin accounts enabled alongside Entra?" "Y"; then
        LOCAL_AUTH_ENABLED_VAL="true"
    else
        LOCAL_AUTH_ENABLED_VAL="false"
        echo "  Local accounts disabled. Users will authenticate via Entra ID only." > /dev/tty 2>/dev/null || true
    fi
fi

# Generate SECRET_KEY — written directly to env file; never printed
SECRET_KEY="$(openssl rand -hex 32)"

# ── 10. Write configuration file ──────────────────────────────────────────────
info "Writing configuration file"

# Create with locked-down permissions before writing any secrets
rm -f "${ENV_FILE}"
touch "${ENV_FILE}"
chown "root:${SVC_USER}" "${ENV_FILE}"
chmod 640 "${ENV_FILE}"

{
    printf '# cklabScheduler environment configuration\n'
    printf '# Generated by deploy/install.sh on %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '# Permissions: root:%s 640  —  do not chmod world-readable\n\n' "${SVC_USER}"

    printf '# Pexip connection\n'
    write_env_line "REG_STATUS_HOST"      "${REG_STATUS_HOST}"
    write_env_line "COMMAND_HOST"         "${COMMAND_HOST}"
    write_env_line "MGMT_USER"            "${MGMT_USER}"
    write_env_line "MGMT_PASS"            "${MGMT_PASS}"
    write_env_line "VERIFY_TLS"           "${VERIFY_TLS}"
    write_env_line "HOST_PIN"             "${HOST_PIN}"
    write_env_line "CONTROL_DISPLAY_NAME" "${CONTROL_DISPLAY_NAME}"
    write_env_line "DIAL_PROTOCOL"        "${DIAL_PROTOCOL}"
    write_env_line "WEBRTC_BASE_URL"      "${WEBRTC_BASE_URL}"
    printf '\n'

    printf '# Scheduler\n'
    write_env_line "APP_DISPLAY_NAME"       "${APP_DISPLAY_NAME}"
    write_env_line "ABOUT_TO_START_MINUTES" "${ABOUT_TO_START_MINUTES}"
    write_env_line "DEFAULT_EXTEND_MINUTES" "${DEFAULT_EXTEND_MINUTES}"
    write_env_line "POLL_SECONDS"           "${POLL_SECONDS}"
    write_env_line "DB_PATH"               "${DB_PATH}"
    printf '\n'

    printf '# Flask session security (auto-generated; do not change unless rotating keys)\n'
    write_env_line "SECRET_KEY" "${SECRET_KEY}"
    printf '\n'

    printf '# Authentication\n'
    write_env_line "LOCAL_AUTH_ENABLED"    "${LOCAL_AUTH_ENABLED_VAL}"
    write_env_line "ENTRA_ENABLED"         "${ENTRA_ENABLED_VAL}"
    write_env_line "SESSION_COOKIE_SECURE" "true"
    if [[ "${ENTRA_ENABLED_VAL}" == "true" ]]; then
        write_env_line "ENTRA_TENANT_ID"                "${ENTRA_TENANT_ID_VAL}"
        write_env_line "ENTRA_CLIENT_ID"                "${ENTRA_CLIENT_ID_VAL}"
        write_env_line "ENTRA_CLIENT_SECRET"            "${ENTRA_CLIENT_SECRET_VAL}"
        write_env_line "ENTRA_AUTHORITY"                "https://login.microsoftonline.com/${ENTRA_TENANT_ID_VAL}"
        write_env_line "ENTRA_REDIRECT_URI"             "${ENTRA_REDIRECT_URI_VAL}"
        write_env_line "ENTRA_POST_LOGOUT_REDIRECT_URI" "${ENTRA_POST_LOGOUT_REDIRECT_URI_VAL}"
    fi
    printf '\n'

    printf '# Microsoft 365\n'
    write_env_line "O365_ENABLED" "${O365_ENABLED_VAL}"
    if [[ "${O365_ENABLED_VAL}" == "true" ]]; then
        write_env_line "O365_TENANT_ID"     "${O365_TENANT_ID}"
        write_env_line "O365_CLIENT_ID"     "${O365_CLIENT_ID}"
        write_env_line "O365_CLIENT_SECRET" "${O365_CLIENT_SECRET}"
        write_env_line "O365_FROM_MAILBOX"  "${O365_FROM_MAILBOX}"
    fi
    write_env_line "O365_EMAIL_SUBJECT"          "${O365_EMAIL_SUBJECT}"
    write_env_line "O365_ORGANIZER_NAME"         "${O365_ORGANIZER_NAME}"
    write_env_line "O365_TIMEZONE"               "${O365_TIMEZONE}"
    write_env_line "O365_LOCATION"               "${O365_LOCATION}"
    write_env_line "O365_INCLUDE_ICS"            "${O365_INCLUDE_ICS_VAL}"
    write_env_line "O365_ALLOW_PROPOSE_NEW_TIME" "${O365_ALLOW_PROPOSE_NEW_TIME_VAL}"
    write_env_line "O365_SAVE_TO_SENT_ITEMS"     "${O365_SAVE_TO_SENT_ITEMS_VAL}"
} >> "${ENV_FILE}"

echo "  Written: ${ENV_FILE} (root:${SVC_USER} 640)"

# ── 11. Database initialisation ───────────────────────────────────────────────
info "Initializing database schema"
(
    cd "${APP_DIR}"
    DB_PATH="${DB_PATH}" "${VENV}/bin/python" \
        -c "from app.database import init_db; init_db()"
)
chown "${SVC_USER}:${SVC_USER}" "${DB_PATH}" 2>/dev/null || true
chmod 640 "${DB_PATH}" 2>/dev/null || true
echo "  Schema ready at ${DB_PATH}."

# ── 12. Create initial admin user ────────────────────────────────────────────
info "Creating initial admin user"
if [[ "${LOCAL_AUTH_ENABLED_VAL}" == "true" ]]; then
    (
        cd "${APP_DIR}"
        # Password is passed via environment variable, not a command-line argument,
        # so it does not appear in the process list.
        ADMIN_USER="${ADMIN_USER}" ADMIN_PASS="${ADMIN_PASS}" \
        DB_PATH="${DB_PATH}" \
        "${VENV}/bin/python" -c "
import os
from app.auth.local import hash_password
from app.auth.models import create_local_user
user = os.environ['ADMIN_USER']
pw   = os.environ['ADMIN_PASS']
create_local_user(user, hash_password(pw), role='administrator')
print('  Admin user created:', user)
"
    )
else
    echo "  Local auth disabled — no local admin user created."
    echo "  Users will authenticate exclusively via Microsoft Entra ID."
fi
# Clear credentials from shell memory
unset ADMIN_PASS ADMIN_USER

# ── 13. Systemd unit files ───────────────────────────────────────────────────
info "Installing systemd unit files"
cp "${SCRIPT_DIR}/cklab-scheduler-web.service"    /etc/systemd/system/
cp "${SCRIPT_DIR}/cklab-scheduler-worker.service" /etc/systemd/system/
systemctl daemon-reload
echo "  Unit files installed and daemon reloaded."

# ── 14. Apache virtual host ───────────────────────────────────────────────────
info "Configuring Apache virtual host"
cat > /etc/apache2/sites-available/cklabscheduler.conf <<APACHECONF
# cklabScheduler Apache virtual host
# Written by deploy/install.sh — edit and re-run install.sh or edit directly.

# HTTP → HTTPS redirect
<VirtualHost *:80>
    ServerName ${SERVER_HOSTNAME}
    Redirect permanent / https://${SERVER_HOSTNAME}/
</VirtualHost>

<VirtualHost *:443>
    ServerName ${SERVER_HOSTNAME}

    SSLEngine on
    SSLCertificateFile    ${CERT_FILE}
    SSLCertificateKeyFile ${KEY_FILE}

    # Exact redirect: /cklabScheduler → /cklabScheduler/
    RedirectMatch permanent ^/cklabScheduler$ /cklabScheduler/

    # Reverse proxy to Gunicorn.  The /cklabScheduler/ prefix is preserved on
    # both sides so Gunicorn's SCRIPT_NAME processing can split the path correctly.
    #
    # Request flow:
    #   Browser   GET /cklabScheduler/api/health
    #   Apache    forwards /cklabScheduler/api/health to http://127.0.0.1:5080/cklabScheduler/api/health
    #   Gunicorn  SCRIPT_NAME=/cklabScheduler → strips prefix → PATH_INFO=/api/health
    #   Flask     routes /api/health; request.script_root=/cklabScheduler
    #
    # DO NOT change this to http://127.0.0.1:5080/ — that strips the prefix and
    # causes Gunicorn IndexError in http/wsgi.py.
    ProxyPreserveHost On
    ProxyPass        /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/
    ProxyPassReverse /cklabScheduler/ http://127.0.0.1:5080/cklabScheduler/

    RequestHeader set X-Forwarded-Proto "https"

    ErrorLog  \${APACHE_LOG_DIR}/cklabscheduler_error.log
    CustomLog \${APACHE_LOG_DIR}/cklabscheduler_access.log combined
</VirtualHost>
APACHECONF

a2ensite cklabscheduler
apache2ctl configtest \
    || die "Apache config test failed — check /etc/apache2/sites-available/cklabscheduler.conf"
systemctl reload apache2
echo "  Apache configured for ${SERVER_HOSTNAME}."

# ── 15. Pre-start validation ─────────────────────────────────────────────────
info "Pre-start validation"

[[ -d "${VENV}" ]] \
    || die "Virtual environment missing: ${VENV}"
echo "  ✓ Virtual environment: ${VENV}"

[[ -x "${VENV}/bin/gunicorn" ]] \
    || die "Gunicorn not found or not executable: ${VENV}/bin/gunicorn — check requirements.txt"
GUNICORN_VER="$("${VENV}/bin/gunicorn" --version 2>&1 || echo 'unknown')"
echo "  ✓ Gunicorn: ${GUNICORN_VER}"

"${VENV}/bin/python" -c "import flask, gunicorn, apscheduler, dotenv, requests" \
    || die "Python module import test failed — run: ${VENV}/bin/pip list"
echo "  ✓ Python modules import successfully"

[[ -f "${ENV_FILE}" ]] \
    || die "Configuration file missing: ${ENV_FILE}"
ENV_PERM_VAL="$(stat -c '%a' "${ENV_FILE}")"
[[ "${ENV_PERM_VAL}" == "640" ]] \
    || die "Config file permissions are ${ENV_PERM_VAL} (expected 640)"
echo "  ✓ Configuration file: ${ENV_FILE} (${ENV_PERM_VAL})"

[[ -f "${DB_PATH}" ]] \
    || die "Database not found: ${DB_PATH} — check database init step"
echo "  ✓ Database: ${DB_PATH}"

echo "  All pre-start checks passed."

# ── 16. Enable and start services ────────────────────────────────────────────
info "Starting services"
systemctl enable "${WEB_SVC}" "${WORKER_SVC}"
systemctl start  "${WEB_SVC}" "${WORKER_SVC}"
echo "  ${WEB_SVC} and ${WORKER_SVC} enabled and started."

# ── 17. Health check ─────────────────────────────────────────────────────────
info "Health check"
echo "  Waiting for services to initialise..."
sleep 5

HEALTH_JSON="$(curl --silent --insecure --max-time 15 \
    "https://localhost/cklabScheduler/api/health" || echo '{}')"

if printf '%s' "${HEALTH_JSON}" | grep -q '"ok": *true'; then
    echo
    echo "  ✓ Installation complete."
    echo
    echo "  Application URL : https://${SERVER_HOSTNAME}/cklabScheduler/"
    echo "  Health endpoint : https://${SERVER_HOSTNAME}/cklabScheduler/api/health"
    if [[ "${LOCAL_AUTH_ENABLED_VAL}" == "true" ]]; then
        echo "  Sign in with the local admin account created during installation."
    else
        echo "  Sign in via Microsoft Entra ID (local accounts are disabled)."
    fi
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
    echo "  Troubleshooting:"
    echo "    journalctl -u ${WEB_SVC}    --no-pager -n 50"
    echo "    journalctl -u ${WORKER_SVC} --no-pager -n 50"
    echo "    apache2ctl configtest"
    echo "    cat ${ENV_FILE}"
    exit 1
fi
