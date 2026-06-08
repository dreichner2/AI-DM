#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
REQUIREMENTS_STAMP="${REPO_ROOT}/.venv/.aidm_requirements.stamp"
BACKEND_PORT="${AIDM_BACKEND_PORT:-5050}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

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

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[backend-service] Creating backend virtualenv"
  command -v python3 >/dev/null 2>&1
  python3 -m venv "${VENV_DIR}"
  "${VENV_PYTHON}" -m pip install --upgrade pip
fi

if requirements_stale; then
  echo "[backend-service] Installing backend dependencies"
  "${VENV_PYTHON}" -m pip install -r "${REPO_ROOT}/requirements.txt"
  touch "${REQUIREMENTS_STAMP}"
fi

cd "${REPO_ROOT}"
exec env AIDM_BACKEND_PORT="${BACKEND_PORT}" ./scripts/run_unified_local.sh
