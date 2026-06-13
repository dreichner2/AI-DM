#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${AIDM_LAUNCHER_LOG_DIR:-${REPO_ROOT}/tmp/launcher_logs}"
LOCK_DIR="${LOG_DIR}/launcher.lock"
LOCK_OWNER_FILE="${LOCK_DIR}/owner"
LOCK_CLEANUP_DIR="${LOG_DIR}/launcher.lock.cleanup"
LOCK_WAIT_ATTEMPTS="${AIDM_LAUNCHER_LOCK_WAIT_ATTEMPTS:-25}"
LOCK_WAIT_SECONDS="${AIDM_LAUNCHER_LOCK_WAIT_SECONDS:-0.2}"
BACKEND_PORT="${AIDM_BACKEND_PORT:-5050}"
BACKEND_HEALTH_URL="http://127.0.0.1:${BACKEND_PORT}/api/health"
APP_URL="${AIDM_APP_URL:-http://127.0.0.1:${BACKEND_PORT}/}"
BACKEND_HELPER_DIR="${HOME}/Library/Application Support/AI-DM"
BACKEND_HELPER="${BACKEND_HELPER_DIR}/launch-backend-service.sh"
BACKEND_ENV_COPY="${BACKEND_HELPER_DIR}/env.local"
BACKEND_PLIST="${HOME}/Library/LaunchAgents/local.aidm.backend.plist"
VENV_DIR="${REPO_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
REQUIREMENTS_STAMP="${VENV_DIR}/.aidm_requirements.stamp"
FRONTEND_DIR="${REPO_ROOT}/aidm_frontend"
FRONTEND_DIST_INDEX="${FRONTEND_DIR}/dist/index.html"
NODE_MODULES_LOCK="${FRONTEND_DIR}/node_modules/.package-lock.json"
LAUNCH_DOMAIN="gui/$(id -u)"
TAILSCALE_BIN="/opt/homebrew/bin/tailscale"
TAILSCALED_BIN="/opt/homebrew/bin/tailscaled"
TAILSCALE_STATE_DIR="${HOME}/.local/share/tailscale"
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
  if [[ "${AIDM_LAUNCHER_SUPPRESS_UI:-0}" == "1" ]]; then
    exit 1
  fi
  /usr/bin/open "${LOG_DIR}" >/dev/null 2>&1 || true
  /usr/bin/osascript - "${message}" "${LOG_DIR}" <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run argv
  set dialogMessage to item 1 of argv & return & return & "Logs: " & item 2 of argv
  display dialog dialogMessage buttons {"OK"} default button "OK" with title "AI-DM Launcher"
end run
APPLESCRIPT
  exit 1
}

acquire_launcher_lock() {
  local token
  token="$(new_launcher_lock_token)"
  if try_acquire_launcher_lock "${token}"; then
    return 0
  fi

  log "Another launcher run is active; waiting briefly."

  local attempt
  for ((attempt = 1; attempt <= LOCK_WAIT_ATTEMPTS; attempt++)); do
    sleep "${LOCK_WAIT_SECONDS}"
    if try_acquire_launcher_lock "${token}"; then
      return 0
    fi
  done

  if take_over_stale_launcher_lock "${token}"; then
    return 0
  fi

  local owner_pid
  owner_pid="$(launcher_lock_value pid || true)"
  if launcher_lock_pid_alive "${owner_pid}"; then
    log "Another launcher run is still active (pid ${owner_pid}); leaving it to finish."
    exit 0
  fi
  fail "Could not acquire launcher lock at ${LOCK_DIR}."
}

new_launcher_lock_token() {
  printf '%s.%s.%s' "$$" "$(date +%s)" "${RANDOM}"
}

launcher_lock_value_from() {
  local owner_file="$1"
  local key="$2"
  [[ -f "${owner_file}" ]] || return 1
  /usr/bin/awk -F= -v key="${key}" '$1 == key { print substr($0, length($1) + 2); exit }' "${owner_file}"
}

launcher_lock_value() {
  launcher_lock_value_from "${LOCK_OWNER_FILE}" "$1"
}

launcher_lock_pid_alive() {
  local pid="$1"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  /bin/kill -0 "${pid}" >/dev/null 2>&1
}

write_launcher_lock_owner() {
  {
    printf 'pid=%s\n' "$$"
    printf 'token=%s\n' "${LOCK_TOKEN}"
    printf 'created_at=%s\n' "$(date +%s)"
  } >"${LOCK_OWNER_FILE}"
}

activate_launcher_lock() {
  LOCK_TOKEN="$1"
  write_launcher_lock_owner || fail "Could not write launcher lock owner at ${LOCK_OWNER_FILE}."
  trap 'release_launcher_lock' EXIT
}

try_acquire_launcher_lock() {
  local token="$1"
  if ! /bin/mkdir "${LOCK_DIR}" 2>/dev/null; then
    return 1
  fi
  if [[ -d "${LOCK_CLEANUP_DIR}" ]]; then
    /bin/rmdir "${LOCK_DIR}" >/dev/null 2>&1 || true
    return 1
  fi
  activate_launcher_lock "${token}"
}

take_over_stale_launcher_lock() {
  local token="$1"
  if ! /bin/mkdir "${LOCK_CLEANUP_DIR}" 2>/dev/null; then
    return 1
  fi

  local owner_pid
  owner_pid="$(launcher_lock_value pid || true)"
  if [[ -z "${owner_pid}" ]]; then
    log "Launcher lock has no owner metadata; not removing it automatically."
    /bin/rmdir "${LOCK_CLEANUP_DIR}" >/dev/null 2>&1 || true
    return 1
  fi
  if launcher_lock_pid_alive "${owner_pid}"; then
    /bin/rmdir "${LOCK_CLEANUP_DIR}" >/dev/null 2>&1 || true
    return 1
  fi

  local stale_dir="${LOCK_DIR}.stale.$$.$RANDOM"
  if [[ -d "${LOCK_DIR}" ]]; then
    if ! /bin/mv "${LOCK_DIR}" "${stale_dir}" 2>/dev/null; then
      /bin/rmdir "${LOCK_CLEANUP_DIR}" >/dev/null 2>&1 || true
      return 1
    fi
    local moved_pid
    moved_pid="$(launcher_lock_value_from "${stale_dir}/owner" pid || true)"
    if [[ -z "${moved_pid}" ]] || launcher_lock_pid_alive "${moved_pid}"; then
      if [[ ! -e "${LOCK_DIR}" ]]; then
        /bin/mv "${stale_dir}" "${LOCK_DIR}" >/dev/null 2>&1 || true
      fi
      /bin/rmdir "${LOCK_CLEANUP_DIR}" >/dev/null 2>&1 || true
      return 1
    fi
    /bin/rm -rf "${stale_dir}"
  fi

  if ! /bin/mkdir "${LOCK_DIR}" 2>/dev/null; then
    /bin/rmdir "${LOCK_CLEANUP_DIR}" >/dev/null 2>&1 || true
    return 1
  fi
  activate_launcher_lock "${token}"
  /bin/rmdir "${LOCK_CLEANUP_DIR}" >/dev/null 2>&1 || true
  log "Removed stale launcher lock."
}

release_launcher_lock() {
  local owner_token
  owner_token="$(launcher_lock_value token || true)"
  if [[ -n "${LOCK_TOKEN:-}" && "${owner_token}" == "${LOCK_TOKEN}" ]]; then
    /bin/rm -f "${LOCK_OWNER_FILE}" >/dev/null 2>&1 || true
    /bin/rmdir "${LOCK_DIR}" >/dev/null 2>&1 || true
  fi
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

backend_launch_agent_pid() {
  /bin/launchctl print "${LAUNCH_DOMAIN}/local.aidm.backend" 2>/dev/null \
    | /usr/bin/awk -F'= ' '/pid =/ { print $2; exit }' \
    | /usr/bin/tr -d ' '
}

launch_agent_loaded() {
  local label="$1"
  /bin/launchctl print "${LAUNCH_DOMAIN}/${label}" >/dev/null 2>&1
}

bootstrap_launch_agent() {
  local label="$1"
  local plist="$2"

  if launch_agent_loaded "${label}"; then
    return 0
  fi

  if /bin/launchctl bootstrap "${LAUNCH_DOMAIN}" "${plist}"; then
    return 0
  fi

  # launchctl may report an error if the service was loaded by a racing double-click.
  launch_agent_loaded "${label}"
}

enable_launch_agent() {
  local label="$1"
  /bin/launchctl enable "${LAUNCH_DOMAIN}/${label}"
}

restart_launch_agent_fast() {
  local label="$1"
  /bin/launchctl kickstart -k "${LAUNCH_DOMAIN}/${label}"
}

stop_non_backend_port_listeners() {
  local pids
  local backend_pid
  local kill_pids=()
  local pid

  pids="$(/usr/sbin/lsof -tiTCP:"${BACKEND_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  [[ -n "${pids}" ]] || return 0

  backend_pid="$(backend_launch_agent_pid)"
  for pid in ${pids}; do
    [[ -n "${backend_pid}" && "${pid}" == "${backend_pid}" ]] && continue
    kill_pids+=("${pid}")
  done

  [[ "${#kill_pids[@]}" -gt 0 ]] || return 0

  log "Stopping stale listener(s) on ${BACKEND_PORT}: ${kill_pids[*]}"
  /bin/kill "${kill_pids[@]}" >/dev/null 2>&1 || true

  local attempt
  for attempt in {1..15}; do
    local still_running=()
    for pid in "${kill_pids[@]}"; do
      if /bin/kill -0 "${pid}" >/dev/null 2>&1; then
        still_running+=("${pid}")
      fi
    done
    [[ "${#still_running[@]}" -eq 0 ]] && return 0
    kill_pids=("${still_running[@]}")
    sleep 0.2
  done

  if [[ "${#kill_pids[@]}" -gt 0 ]]; then
    /bin/kill -9 "${kill_pids[@]}" >/dev/null 2>&1 || true
  fi
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local max_seconds="${3:-90}"
  local start
  local now

  start="$(date +%s)"
  while true; do
    if /usr/bin/curl -fsS --connect-timeout 1 --max-time 1 "${url}" >/dev/null 2>&1; then
      log "${label} ready at ${url}"
      return 0
    fi

    now="$(date +%s)"
    if (( now - start >= max_seconds )); then
      return 1
    fi
    sleep 0.2
  done
}

wait_for_unified_app() {
  local max_seconds="${1:-90}"
  local start
  local now
  local body

  start="$(date +%s)"
  while true; do
    body="$(/usr/bin/curl -fsS --connect-timeout 1 --max-time 1 "${APP_URL}" 2>/dev/null || true)"
    if [[ "${body}" == *"AI-DM Tabletop Console"* || "${body}" == *'id="root"'* ]]; then
      log "Unified app ready at ${APP_URL}"
      return 0
    fi

    now="$(date +%s)"
    if (( now - start >= max_seconds )); then
      return 1
    fi
    sleep 0.2
  done
}

requirements_stale() {
  [[ -f "${REQUIREMENTS_STAMP}" ]] || return 0

  local file
  for file in \
    "${REPO_ROOT}/requirements.txt" \
    "${REPO_ROOT}/requirements-dev.txt" \
    "${REPO_ROOT}/requirements.runtime.txt" \
    "${REPO_ROOT}/requirements.constraints.txt"; do
    [[ -f "${file}" && "${file}" -nt "${REQUIREMENTS_STAMP}" ]] && return 0
  done

  return 1
}

ensure_backend_dependencies() {
  if [[ ! -x "${VENV_PYTHON}" ]]; then
    log "Creating backend virtualenv."
    command -v python3 >/dev/null 2>&1
    python3 -m venv "${VENV_DIR}"
    "${VENV_PYTHON}" -m pip install --upgrade pip
  fi

  if requirements_stale; then
    log "Installing updated backend dependencies."
    "${VENV_PYTHON}" -m pip install -r "${REPO_ROOT}/requirements.txt"
    touch "${REQUIREMENTS_STAMP}"
  fi
}

frontend_dist_ready() {
  [[ -f "${FRONTEND_DIST_INDEX}" ]] || return 1
  /usr/bin/grep -q 'id="root"' "${FRONTEND_DIST_INDEX}" || return 1
}

frontend_dist_stale() {
  [[ "${AIDM_FRONTEND_BUILD_MODE:-auto}" == "always" ]] && return 0
  [[ "${AIDM_FRONTEND_BUILD_MODE:-auto}" == "skip" ]] && return 1
  frontend_dist_ready || return 0

  local newer
  newer="$(
    /usr/bin/find \
      "${FRONTEND_DIR}/src" \
      "${FRONTEND_DIR}/public" \
      "${FRONTEND_DIR}/package.json" \
      "${FRONTEND_DIR}/package-lock.json" \
      "${FRONTEND_DIR}/tsconfig.json" \
      "${FRONTEND_DIR}/tsconfig.app.json" \
      "${FRONTEND_DIR}/vite.config.ts" \
      -newer "${FRONTEND_DIST_INDEX}" \
      -print -quit 2>/dev/null || true
  )"
  [[ -n "${newer}" ]]
}

ensure_npm() {
  if command -v npm >/dev/null 2>&1; then
    return 0
  fi

  export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"
  if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    . "${NVM_DIR}/nvm.sh"
    nvm use --silent default >/dev/null 2>&1 || nvm use --silent node >/dev/null 2>&1 || true
  fi

  command -v npm >/dev/null 2>&1
}

frontend_dependencies_stale() {
  [[ -d "${FRONTEND_DIR}/node_modules" && -f "${NODE_MODULES_LOCK}" ]] || return 0
  [[ "${FRONTEND_DIR}/package.json" -nt "${NODE_MODULES_LOCK}" ]] && return 0
  [[ "${FRONTEND_DIR}/package-lock.json" -nt "${NODE_MODULES_LOCK}" ]] && return 0
  return 1
}

ensure_frontend_build() {
  if ! frontend_dist_stale; then
    log "Frontend build is current."
    return 0
  fi

  ensure_npm

  if frontend_dependencies_stale; then
    log "Installing updated frontend dependencies."
    cd "${FRONTEND_DIR}"
    npm ci
  fi

  log "Building updated frontend."
  cd "${FRONTEND_DIR}"
  env VITE_AIDM_API_BASE_URL= npm run build
  cd "${REPO_ROOT}"
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

write_backend_launch_agent() {
  mkdir -p "${BACKEND_HELPER_DIR}" "${HOME}/Library/LaunchAgents" "${LOG_DIR}"
  if [[ -f "${REPO_ROOT}/.env.local" ]]; then
    /bin/cp "${REPO_ROOT}/.env.local" "${BACKEND_ENV_COPY}"
    /bin/chmod 600 "${BACKEND_ENV_COPY}"
  fi

  cat >"${BACKEND_HELPER}" <<SCRIPT
#!/bin/bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT}"
BACKEND_PORT="\${AIDM_BACKEND_PORT:-${BACKEND_PORT}}"
ENV_FILE="${BACKEND_ENV_COPY}"
VENV_PYTHON="${VENV_PYTHON}"
FRONTEND_DIR="\${REPO_ROOT}/aidm_frontend"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:\${PATH:-}"

cd "\${REPO_ROOT}"
echo "[unified-local] Starting unified AIDM on http://127.0.0.1:\${BACKEND_PORT}/"

if [[ -f "\${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "\${ENV_FILE}"
  set +a
fi

if [[ -z "\${AIDM_LLM_PROVIDER:-}" ]]; then
  if [[ -n "\${GOOGLE_GENAI_API_KEY:-}" ]]; then
    export AIDM_LLM_PROVIDER="gemini"
  elif [[ -n "\${AIDM_DEEPSEEK_API_KEY:-}" || -n "\${DEEPSEEK_API_KEY:-}" ]]; then
    export AIDM_LLM_PROVIDER="deepseek"
  elif [[ -n "\${AIDM_NVIDIA_API_KEY:-}" || -n "\${NVIDIA_API_KEY:-}" ]]; then
    export AIDM_LLM_PROVIDER="nvidia"
  else
    export AIDM_LLM_PROVIDER="fallback"
  fi
fi

if [[ "\${AIDM_LLM_PROVIDER}" == "deepseek" ]]; then
  export AIDM_LLM_MODEL="\${AIDM_LLM_MODEL:-deepseek-v4-pro}"
  export AIDM_LLM_FALLBACK_MODELS="\${AIDM_LLM_FALLBACK_MODELS:-}"
  export AIDM_DEEPSEEK_BASE_URL="\${AIDM_DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
  export AIDM_DEEPSEEK_API_KEY="\${AIDM_DEEPSEEK_API_KEY:-\${DEEPSEEK_API_KEY:-}}"
elif [[ "\${AIDM_LLM_PROVIDER}" == "nvidia" || "\${AIDM_LLM_PROVIDER}" == "kimi" ]]; then
  export AIDM_LLM_MODEL="\${AIDM_LLM_MODEL:-moonshotai/kimi-k2.5}"
  export AIDM_LLM_FALLBACK_MODELS="\${AIDM_LLM_FALLBACK_MODELS:-}"
  export AIDM_NVIDIA_INVOKE_URL="\${AIDM_NVIDIA_INVOKE_URL:-https://integrate.api.nvidia.com/v1}"
elif [[ "\${AIDM_LLM_PROVIDER}" == "codex" || "\${AIDM_LLM_PROVIDER}" == "codex_cli" ]]; then
  export AIDM_LLM_MODEL="\${AIDM_LLM_MODEL:-gpt-5.5-medium}"
  export AIDM_LLM_FALLBACK_MODELS="\${AIDM_LLM_FALLBACK_MODELS:-}"
  export AIDM_CODEX_REASONING_EFFORT="\${AIDM_CODEX_REASONING_EFFORT:-medium}"
  export AIDM_CODEX_TIMEOUT_SECONDS="\${AIDM_CODEX_TIMEOUT_SECONDS:-240}"
elif [[ "\${AIDM_LLM_PROVIDER}" == "fallback" ]]; then
  export AIDM_LLM_MODEL="\${AIDM_LLM_MODEL:-deterministic-v1}"
  export AIDM_LLM_FALLBACK_MODELS="\${AIDM_LLM_FALLBACK_MODELS:-}"
else
  export AIDM_LLM_MODEL="\${AIDM_LLM_MODEL:-models/gemini-3-flash-preview}"
  export AIDM_LLM_FALLBACK_MODELS="\${AIDM_LLM_FALLBACK_MODELS:-models/gemini-2.5-flash}"
fi

export AIDM_DEBUG="\${AIDM_DEBUG:-false}"
export AIDM_SERVE_FRONTEND=true
export AIDM_FRONTEND_DIST_DIR="\${FRONTEND_DIR}/dist"
export PORT="\${BACKEND_PORT}"
export AIDM_ENV_FILE="\${ENV_FILE}"
export AIDM_SKIP_REPO_ENV_LOCAL=1
export AIDM_CORS_ALLOWLIST="\${AIDM_CORS_ALLOWLIST-}"
export AIDM_SOCKET_CORS_ALLOWLIST="\${AIDM_SOCKET_CORS_ALLOWLIST-\${AIDM_CORS_ALLOWLIST}}"
export AIDM_CORS_ALLOW_PRIVATE_NETWORK="\${AIDM_CORS_ALLOW_PRIVATE_NETWORK:-true}"

exec env \\
  AIDM_BACKEND_PORT="\${BACKEND_PORT}" \\
  "\${VENV_PYTHON}" "\${REPO_ROOT}/scripts/deploy_bootstrap.py" --port "\${BACKEND_PORT}"
SCRIPT
  /bin/chmod +x "${BACKEND_HELPER}"

  local next_plist="${BACKEND_PLIST}.next"
  cat >"${next_plist}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.aidm.backend</string>
  <key>ProgramArguments</key>
  <array>
    <string>${BACKEND_HELPER}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>AIDM_BACKEND_PORT</key>
    <string>${BACKEND_PORT}</string>
    <key>AIDM_FRONTEND_BUILD_MODE</key>
    <string>${AIDM_FRONTEND_BUILD_MODE:-auto}</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>${BACKEND_HELPER_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/backend.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/backend.log</string>
</dict>
</plist>
PLIST

  if [[ ! -f "${BACKEND_PLIST}" ]] || ! /usr/bin/cmp -s "${next_plist}" "${BACKEND_PLIST}"; then
    /bin/mv "${next_plist}" "${BACKEND_PLIST}"
    /bin/launchctl bootout "${LAUNCH_DOMAIN}/local.aidm.backend" >/dev/null 2>&1 || true
  else
    /bin/rm -f "${next_plist}"
  fi
}

start_backend_launch_agent() {
  write_backend_launch_agent
  stop_non_backend_port_listeners
  bootstrap_launch_agent "local.aidm.backend" "${BACKEND_PLIST}" || return 1
  enable_launch_agent "local.aidm.backend" || return 1
  restart_launch_agent_fast "local.aidm.backend" || return 1
}

write_tailscale_launch_agent() {
  [[ -x "${TAILSCALED_BIN}" ]] || return 1

  mkdir -p "${HOME}/Library/LaunchAgents" "${TAILSCALE_STATE_DIR}" "${LOG_DIR}"

  local next_plist="${TAILSCALE_PLIST}.next"
  cat >"${next_plist}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.aidm.tailscaled</string>
  <key>ProgramArguments</key>
  <array>
    <string>${TAILSCALED_BIN}</string>
    <string>--tun=userspace-networking</string>
    <string>--socket=${TAILSCALE_SOCKET_PATH}</string>
    <string>--state=${TAILSCALE_STATE_DIR}/tailscaled.state</string>
    <string>--statedir=${TAILSCALE_STATE_DIR}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${TAILSCALE_STATE_DIR}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/tailscaled.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/tailscaled.log</string>
</dict>
</plist>
PLIST

  if [[ ! -f "${TAILSCALE_PLIST}" ]] || ! /usr/bin/cmp -s "${next_plist}" "${TAILSCALE_PLIST}"; then
    /bin/mv "${next_plist}" "${TAILSCALE_PLIST}"
    /bin/launchctl bootout "${LAUNCH_DOMAIN}/local.aidm.tailscaled" >/dev/null 2>&1 || true
  else
    /bin/rm -f "${next_plist}"
  fi
}

tailscale_running() {
  "${TAILSCALE_BIN}" --socket="${TAILSCALE_SOCKET_PATH}" status --json 2>/dev/null \
    | /usr/bin/grep -q '"BackendState": "Running"'
}

ensure_tailscale_daemon() {
  [[ -x "${TAILSCALE_BIN}" && -x "${TAILSCALED_BIN}" ]] || return 1
  write_tailscale_launch_agent || return 1

  bootstrap_launch_agent "local.aidm.tailscaled" "${TAILSCALE_PLIST}" || return 1
  enable_launch_agent "local.aidm.tailscaled" || return 1

  if tailscale_running; then
    return 0
  fi

  if [[ -S "${TAILSCALE_SOCKET_PATH}" ]] && ! /usr/sbin/lsof "${TAILSCALE_SOCKET_PATH}" >/dev/null 2>&1; then
    /bin/rm -f "${TAILSCALE_SOCKET_PATH}" >/dev/null 2>&1 || true
  fi

  restart_launch_agent_fast "local.aidm.tailscaled" || return 1

  local attempt
  for attempt in {1..20}; do
    if tailscale_running; then
      return 0
    fi
    sleep 0.2
  done

  return 1
}

start_tailscale_funnel() {
  if ensure_tailscale_daemon; then
    if "${TAILSCALE_BIN}" --socket="${TAILSCALE_SOCKET_PATH}" funnel --bg --yes "${BACKEND_PORT}" >/dev/null 2>&1; then
      log "Tailscale Funnel ensured on ${BACKEND_PORT}."
    else
      log "Tailscale is running, but Funnel was not started automatically."
    fi
  else
    log "Tailscale daemon did not become ready; public Funnel URL not started."
  fi
}

start_backend() {
  if port_open "${BACKEND_PORT}"; then
    log "Restarting unified AI-DM on ${BACKEND_PORT}."
  else
    log "Starting unified AI-DM on ${BACKEND_PORT}."
  fi
  ensure_backend_dependencies
  ensure_frontend_build
  start_backend_launch_agent || fail "Could not start backend LaunchAgent."
  wait_for_http "${BACKEND_HEALTH_URL}" "Backend" 5 || fail "Backend did not become ready at ${BACKEND_HEALTH_URL}."
}

main() {
  log "Launch requested at $(date)"
  acquire_launcher_lock
  stop_legacy_frontend
  start_backend
  wait_for_http "${BACKEND_HEALTH_URL}" "Backend" 120 || fail "Backend did not become ready at ${BACKEND_HEALTH_URL}."
  wait_for_unified_app 120 || fail "Unified app did not become ready at ${APP_URL}."

  /usr/bin/open "${APP_URL}"
  log "Opened ${APP_URL}"

  start_tailscale_funnel
  /usr/bin/osascript -e 'display notification "Unified AI-DM is running." with title "AI-DM Launcher"' >/dev/null 2>&1 || true
}

if [[ "${AIDM_LAUNCHER_SOURCE_ONLY:-0}" != "1" ]]; then
  main >>"${LOG_DIR}/launcher.log" 2>&1
fi
