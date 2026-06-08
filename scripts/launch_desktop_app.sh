#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${REPO_ROOT}/tmp/launcher_logs"
BACKEND_PORT="${AIDM_BACKEND_PORT:-5050}"
BACKEND_HEALTH_URL="http://127.0.0.1:${BACKEND_PORT}/api/health"
APP_URL="${AIDM_APP_URL:-http://127.0.0.1:${BACKEND_PORT}/}"
LAUNCH_DOMAIN="gui/$(id -u)"
TAILSCALE_BIN="/opt/homebrew/bin/tailscale"
TAILSCALE_SOCKET_PATH="${HOME}/.local/share/tailscale/tailscaled.sock"
TAILSCALE_PLIST="${HOME}/Library/LaunchAgents/local.aidm.tailscaled.plist"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

mkdir -p "${LOG_DIR}"
bash "${REPO_ROOT}/scripts/prune_launcher_logs.sh" "${LOG_DIR}" >/dev/null 2>&1 || true

log() {
  printf '[launcher] %s\n' "$*"
}

fail() {
  local message="$1"
  log "ERROR: ${message}"
  /usr/bin/open "${LOG_DIR}" >/dev/null 2>&1 || true
  /usr/bin/osascript - "${message}" "${LOG_DIR}" <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run argv
  set dialogMessage to item 1 of argv & return & return & "Logs: " & item 2 of argv
  display dialog dialogMessage buttons {"OK"} default button "OK" with title "AI-DM Launcher"
end run
APPLESCRIPT
  exit 1
}

xml_escape() {
  local value="$1"
  value="${value//&/&amp;}"
  value="${value//</&lt;}"
  value="${value//>/&gt;}"
  value="${value//\"/&quot;}"
  printf '%s' "${value}"
}

render_launch_agent_plist() {
  local label="$1"
  local source_plist="$2"
  local target_plist="$3"
  local program_path
  local working_dir
  local log_path
  local rendered

  case "${label}" in
    local.aidm.backend)
      program_path="${REPO_ROOT}/scripts/launch_backend_service.sh"
      working_dir="${REPO_ROOT}"
      log_path="${LOG_DIR}/backend.log"
      ;;
    *)
      fail "Unknown LaunchAgent label ${label}."
      ;;
  esac

  rendered="$(<"${source_plist}")"
  rendered="${rendered//__AIDM_PROGRAM__/$(xml_escape "${program_path}")}"
  rendered="${rendered//__AIDM_WORKING_DIRECTORY__/$(xml_escape "${working_dir}")}"
  rendered="${rendered//__AIDM_STDOUT_LOG__/$(xml_escape "${log_path}")}"
  rendered="${rendered//__AIDM_STDERR_LOG__/$(xml_escape "${log_path}")}"
  printf '%s\n' "${rendered}" >"${target_plist}"
}

port_open() {
  /usr/sbin/lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

stop_backend_port_listeners() {
  local pids
  pids="$(/usr/sbin/lsof -tiTCP:"${BACKEND_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "${pids}" ]] || return 0

  log "Stopping stale listener(s) on ${BACKEND_PORT}: ${pids//$'\n'/ }"
  # shellcheck disable=SC2086
  /bin/kill ${pids} >/dev/null 2>&1 || true

  local start
  local now
  start="$(date +%s)"
  while port_open "${BACKEND_PORT}"; do
    now="$(date +%s)"
    if (( now - start >= 10 )); then
      pids="$(/usr/sbin/lsof -tiTCP:"${BACKEND_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
      if [[ -n "${pids}" ]]; then
        # shellcheck disable=SC2086
        /bin/kill -9 ${pids} >/dev/null 2>&1 || true
      fi
      break
    fi
    sleep 1
  done
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local max_seconds="${3:-90}"
  local start
  local now

  start="$(date +%s)"
  while true; do
    if /usr/bin/curl -fsS --connect-timeout 2 --max-time 4 "${url}" >/dev/null 2>&1; then
      log "${label} ready at ${url}"
      return 0
    fi

    now="$(date +%s)"
    if (( now - start >= max_seconds )); then
      return 1
    fi
    sleep 1
  done
}

wait_for_unified_app() {
  local max_seconds="${1:-90}"
  local start
  local now
  local body

  start="$(date +%s)"
  while true; do
    body="$(/usr/bin/curl -fsS --connect-timeout 2 --max-time 4 "${APP_URL}" 2>/dev/null || true)"
    if [[ "${body}" == *"AI-DM Tabletop Console"* || "${body}" == *'id="root"'* ]]; then
      log "Unified app ready at ${APP_URL}"
      return 0
    fi

    now="$(date +%s)"
    if (( now - start >= max_seconds )); then
      return 1
    fi
    sleep 1
  done
}

install_launch_agent() {
  local label="$1"
  local source_plist="${REPO_ROOT}/scripts/launchd/${label}.plist"
  local target_plist="${HOME}/Library/LaunchAgents/${label}.plist"
  local next_plist="${target_plist}.next"

  [[ -f "${source_plist}" ]] || fail "Missing LaunchAgent template at ${source_plist}."
  mkdir -p "${HOME}/Library/LaunchAgents"
  render_launch_agent_plist "${label}" "${source_plist}" "${next_plist}"

  if [[ ! -f "${target_plist}" ]] || ! /usr/bin/cmp -s "${next_plist}" "${target_plist}"; then
    /bin/mv "${next_plist}" "${target_plist}"
    /bin/launchctl bootout "${LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1 || true
  else
    /bin/rm -f "${next_plist}"
  fi

  /bin/launchctl bootstrap "${LAUNCH_DOMAIN}" "${target_plist}" >/dev/null 2>&1 || true
  /bin/launchctl enable "${LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1 || true
}

kickstart_launch_agent() {
  local label="$1"
  /bin/launchctl kickstart "${LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1 || fail "Could not start ${label} LaunchAgent."
}

restart_launch_agent() {
  local label="$1"
  /bin/launchctl kickstart -k "${LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1 || fail "Could not restart ${label} LaunchAgent."
}

stop_legacy_frontend() {
  /bin/launchctl bootout "${LAUNCH_DOMAIN}/local.aidm.frontend" >/dev/null 2>&1 || true
}

start_tailscale_funnel() {
  if [[ ! -x "${TAILSCALE_BIN}" || ! -f "${TAILSCALE_PLIST}" ]]; then
    return 0
  fi

  /bin/launchctl bootstrap "${LAUNCH_DOMAIN}" "${TAILSCALE_PLIST}" >/dev/null 2>&1 || true
  /bin/launchctl enable "${LAUNCH_DOMAIN}/local.aidm.tailscaled" >/dev/null 2>&1 || true
  if ! "${TAILSCALE_BIN}" --socket="${TAILSCALE_SOCKET_PATH}" status --json >/dev/null 2>&1; then
    /bin/launchctl kickstart -k "${LAUNCH_DOMAIN}/local.aidm.tailscaled" >/dev/null 2>&1 || true
  fi

  if "${TAILSCALE_BIN}" --socket="${TAILSCALE_SOCKET_PATH}" status --json 2>/dev/null | /usr/bin/grep -q '"BackendState": "Running"'; then
    "${REPO_ROOT}/scripts/aidm_tailscale.sh" funnel-on >/dev/null 2>&1 || log "Tailscale Funnel was not started automatically."
    log "Tailscale Funnel checked."
  else
    log "Tailscale is not logged in; public Funnel URL not started."
  fi
}

start_backend() {
  install_launch_agent "local.aidm.backend"

  if wait_for_http "${BACKEND_HEALTH_URL}" "Backend" 2; then
    if wait_for_unified_app 2; then
      log "Existing unified AI-DM is healthy; restarting backend to reload local configuration."
    else
      log "Backend health is up, but unified frontend is not being served; clearing stale API-only process."
      stop_backend_port_listeners
    fi
  else
    log "Starting unified AI-DM LaunchAgent on ${BACKEND_PORT}"
  fi

  restart_launch_agent "local.aidm.backend"
}

{
  log "Launch requested at $(date)"
  stop_legacy_frontend
  start_backend
  wait_for_http "${BACKEND_HEALTH_URL}" "Backend" 120 || fail "Backend did not become ready at ${BACKEND_HEALTH_URL}."
  wait_for_unified_app 120 || fail "Unified app did not become ready at ${APP_URL}."
  start_tailscale_funnel

  /usr/bin/open "${APP_URL}"
  /usr/bin/osascript -e 'display notification "Unified AI-DM is running." with title "AI-DM Launcher"' >/dev/null 2>&1 || true
  log "Opened ${APP_URL}"
} >>"${LOG_DIR}/launcher.log" 2>&1
