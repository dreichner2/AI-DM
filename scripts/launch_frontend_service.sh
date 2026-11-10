#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/aidm_frontend"
FRONTEND_PORT="${AIDM_FRONTEND_PORT:-5173}"
BACKEND_PORT="${AIDM_BACKEND_PORT:-5050}"
FRONTEND_BACKEND_URL="${VITE_AIDM_API_BASE_URL:-http://127.0.0.1:${BACKEND_PORT}}"

export PATH="${HOME}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

if ! command -v npm >/dev/null 2>&1; then
  export NVM_DIR="${NVM_DIR:-${HOME}/.nvm}"
  if [[ -s "${NVM_DIR}/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    . "${NVM_DIR}/nvm.sh"
    nvm use --silent "$(<"${REPO_ROOT}/.nvmrc")" >/dev/null 2>&1 || true
  fi
fi

command -v npm >/dev/null 2>&1
# Same rule as scripts/check_toolchain.py, inlined so it needs no Python.
node -e 'const version = process.versions.node; const [major, minor] = version.split(".").map(Number); if (major !== 24 || minor < 18) { console.error(`Node.js 24 (24.18.0 or newer) is required; found ${version}.`); process.exit(1); }'
npm_version="$(npm --version)"
[[ "${npm_version}" == 12.* ]] || { echo "npm 12 is required; found ${npm_version}." >&2; exit 1; }

if [[ ! -d "${FRONTEND_DIR}/node_modules" ]]; then
  echo "[frontend-service] Installing frontend dependencies"
  cd "${FRONTEND_DIR}"
  npm ci
fi

cd "${FRONTEND_DIR}"
exec env VITE_AIDM_API_BASE_URL="${FRONTEND_BACKEND_URL}" npm run dev -- --host 127.0.0.1 --port "${FRONTEND_PORT}" --strictPort
