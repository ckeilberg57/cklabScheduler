#!/usr/bin/env bash
# deploy/uninstall.sh — Interactive uninstaller for cklabScheduler
# Asks separately before removing code, configuration, data, or the service
# account.  Defaults to preserving configuration and database data.
set -euo pipefail

# ── Constants ────────────────────────────────────────────────────────────────
APP_DIR="/opt/cklabScheduler"
CONF_DIR="/etc/cklabScheduler"
DATA_DIR="/var/lib/cklabScheduler"
SVC_USER="cklabscheduler"
WEB_SVC="cklab-scheduler-web"
WORKER_SVC="cklab-scheduler-worker"

# ── Helpers ──────────────────────────────────────────────────────────────────
die()  { echo; echo "FATAL: $*" >&2; exit 1; }
info() { echo; printf '══ %s ══\n' "$*"; }

prompt_yesno() {
    local prompt="$1" default="${2:-N}" reply
    printf '  %s [%s]: ' "${prompt}" "${default}" > /dev/tty
    read -r reply < /dev/tty
    reply="${reply:-${default}}"
    [[ "${reply,,}" =~ ^y(es)?$ ]]
}

removed=()
kept=()

# ── Root check ───────────────────────────────────────────────────────────────
[[ "${EUID}" -eq 0 ]] || die "This script must be run as root."

# ── 1. Confirm ───────────────────────────────────────────────────────────────
info "cklabScheduler Uninstaller"
echo
echo "  This will stop and remove cklabScheduler services."
echo "  You will be asked separately about code, configuration,"
echo "  database data, and the service account."
echo
prompt_yesno "Continue with uninstall?" "N" || { echo "  Aborted."; exit 0; }

# ── 2. Stop and disable services ─────────────────────────────────────────────
info "Stopping services"
systemctl stop    "${WEB_SVC}"    2>/dev/null && echo "  Stopped ${WEB_SVC}."    || echo "  ${WEB_SVC} was not running."
systemctl stop    "${WORKER_SVC}" 2>/dev/null && echo "  Stopped ${WORKER_SVC}." || echo "  ${WORKER_SVC} was not running."
systemctl disable "${WEB_SVC}"    2>/dev/null || true
systemctl disable "${WORKER_SVC}" 2>/dev/null || true

# ── 3. Remove systemd unit files ──────────────────────────────────────────────
info "Removing systemd unit files"
rm -f /etc/systemd/system/cklab-scheduler-web.service
rm -f /etc/systemd/system/cklab-scheduler-worker.service
systemctl daemon-reload
echo "  Unit files removed."
removed+=("systemd units")

# ── 4. Remove Apache configuration ───────────────────────────────────────────
info "Removing Apache configuration"
a2dissite cklabscheduler 2>/dev/null || true
rm -f /etc/apache2/sites-available/cklabscheduler.conf
systemctl reload apache2 2>/dev/null || true
echo "  Apache site configuration removed."
removed+=("Apache vhost config")

# ── 5. Application code ───────────────────────────────────────────────────────
info "Application code"
if [[ -d "${APP_DIR}" ]]; then
    echo
    if prompt_yesno "Remove application code at ${APP_DIR}?" "N"; then
        rm -rf "${APP_DIR}"
        echo "  Removed ${APP_DIR}."
        removed+=("application code (${APP_DIR})")
    else
        echo "  Kept ${APP_DIR}."
        kept+=("application code (${APP_DIR})")
    fi
else
    echo "  ${APP_DIR} not found — nothing to remove."
fi

# ── 6. Configuration ──────────────────────────────────────────────────────────
info "Configuration"
if [[ -d "${CONF_DIR}" ]]; then
    echo
    echo "  WARNING: ${CONF_DIR} contains Pexip credentials and O365 secrets."
    echo
    if prompt_yesno "Remove configuration at ${CONF_DIR}?" "N"; then
        rm -rf "${CONF_DIR}"
        echo "  Removed ${CONF_DIR}."
        removed+=("configuration (${CONF_DIR})")
    else
        echo "  Kept ${CONF_DIR}."
        kept+=("configuration (${CONF_DIR})")
    fi
else
    echo "  ${CONF_DIR} not found — nothing to remove."
fi

# ── 7. Database and meeting data ─────────────────────────────────────────────
info "Database and meeting data"
if [[ -d "${DATA_DIR}" ]]; then
    echo
    echo "  WARNING: THIS IS PERMANENT."
    echo "  All scheduled and historical meeting data in ${DATA_DIR} will be lost."
    echo
    if prompt_yesno "Remove database data at ${DATA_DIR}?" "N"; then
        rm -rf "${DATA_DIR}"
        echo "  Removed ${DATA_DIR}."
        removed+=("database data (${DATA_DIR})")
    else
        echo "  Kept ${DATA_DIR}."
        kept+=("database data (${DATA_DIR})")
    fi
else
    echo "  ${DATA_DIR} not found — nothing to remove."
fi

# ── 8. Service account ────────────────────────────────────────────────────────
info "Service account"
if id "${SVC_USER}" &>/dev/null; then
    echo
    if prompt_yesno "Remove service account '${SVC_USER}'?" "N"; then
        userdel "${SVC_USER}" 2>/dev/null || true
        echo "  Removed account '${SVC_USER}'."
        removed+=("service account '${SVC_USER}'")
    else
        echo "  Kept account '${SVC_USER}'."
        kept+=("service account '${SVC_USER}'")
    fi
else
    echo "  Account '${SVC_USER}' not found — nothing to remove."
fi

# ── 9. Summary ────────────────────────────────────────────────────────────────
info "Uninstall summary"
echo
if [[ "${#removed[@]}" -gt 0 ]]; then
    echo "  Removed:"
    for item in "${removed[@]}"; do
        echo "    ✓ ${item}"
    done
fi
if [[ "${#kept[@]}" -gt 0 ]]; then
    echo
    echo "  Preserved:"
    for item in "${kept[@]}"; do
        echo "    – ${item}"
    done
fi
echo
echo "  Done."
