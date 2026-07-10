#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/aidm_frontend"
FRONTEND_DIST_INDEX="${FRONTEND_DIR}/dist/index.html"
NODE_MODULES_LOCK="${FRONTEND_DIR}/node_modules/.package-lock.json"
BACKEND_PORT="${AIDM_BACKEND_PORT:-5050}"
FRONTEND_BUILD_MODE="${AIDM_FRONTEND_BUILD_MODE:-auto}"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

frontend_dist_ready() {
  [[ -f "${FRONTEND_DIST_INDEX}" ]] || return 1
  /usr/bin/grep -q 'id="root"' "${FRONTEND_DIST_INDEX}" || return 1
}

frontend_dist_stale() {
  [[ "${FRONTEND_BUILD_MODE}" == "always" ]] && return 0
  [[ "${FRONTEND_BUILD_MODE}" == "skip" ]] && return 1
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
  if ! command -v npm >/dev/null 2>&1; then
    export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"
    if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
      # shellcheck disable=SC1091
      . "${NVM_DIR}/nvm.sh"
      nvm use --silent "$(<"${REPO_ROOT}/.nvmrc")" >/dev/null 2>&1 || true
    fi
  fi

  command -v npm >/dev/null 2>&1
  node -e 'process.exit(process.versions.node === "24.18.0" ? 0 : 1)'
  [[ "$(npm --version)" == "12.0.0" ]]
}

frontend_dependencies_stale() {
  [[ -d "${FRONTEND_DIR}/node_modules" && -f "${NODE_MODULES_LOCK}" ]] || return 0
  [[ "${FRONTEND_DIR}/package.json" -nt "${NODE_MODULES_LOCK}" ]] && return 0
  [[ "${FRONTEND_DIR}/package-lock.json" -nt "${NODE_MODULES_LOCK}" ]] && return 0
  return 1
}

if frontend_dist_stale; then
  ensure_npm

  if frontend_dependencies_stale; then
    echo "[unified-local] Installing frontend dependencies"
    cd "${FRONTEND_DIR}"
    npm ci
  fi

  echo "[unified-local] Building frontend for same-origin backend"
  cd "${FRONTEND_DIR}"
  env VITE_AIDM_API_BASE_URL= npm run build
else
  echo "[unified-local] Reusing existing frontend build"
fi

cd "${REPO_ROOT}"
echo "[unified-local] Starting unified AIDM on http://127.0.0.1:${BACKEND_PORT}/"
exec env \
  AIDM_SERVE_FRONTEND=true \
  AIDM_FRONTEND_DIST_DIR="${FRONTEND_DIR}/dist" \
  PORT="${BACKEND_PORT}" \
  ./scripts/run_local_backend.sh
