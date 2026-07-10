#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-5050}"
BIND="${AIDM_BIND:-0.0.0.0:${PORT}}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
GUNICORN_THREADS="${AIDM_GUNICORN_THREADS:-100}"
GUNICORN_TIMEOUT="${AIDM_GUNICORN_TIMEOUT:-180}"
PREFLIGHT_HOST="${AIDM_PREFLIGHT_HOST:-0.0.0.0}"

if [[ "${AIDM_ENV:-}" != "production" ]]; then
  echo "AIDM_ENV must be explicitly set to production for scripts/run_production_server.sh." >&2
  exit 2
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  RESOLVED_PYTHON_BIN="${PYTHON_BIN}"
elif [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  RESOLVED_PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
elif command -v python3.14 >/dev/null 2>&1; then
  RESOLVED_PYTHON_BIN="$(command -v python3.14)"
else
  echo "Python 3.14 was not found. Create .venv with make install or set PYTHON_BIN." >&2
  exit 127
fi

if ! "${RESOLVED_PYTHON_BIN}" -c 'import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 14, 6) else 1)'; then
  echo "scripts/run_production_server.sh requires Python 3.14.6." >&2
  exit 2
fi

if [[ "${AIDM_LLM_PROVIDER:-}" == "codex_cli" || "${AIDM_LLM_PROVIDER:-}" == "codex" ]]; then
  REQUESTED_CODEX_EXECUTABLE="${AIDM_CODEX_EXECUTABLE:-codex}"
  RESOLVED_CODEX_EXECUTABLE="$(
    PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}" "${RESOLVED_PYTHON_BIN}" -c \
      'import sys; from aidm_server.codex_runtime import resolve_codex_executable; print(resolve_codex_executable(sys.argv[1]) or "")' \
      "${REQUESTED_CODEX_EXECUTABLE}"
  )"
  if [[ -z "${RESOLVED_CODEX_EXECUTABLE}" ]]; then
    echo "AIDM_LLM_PROVIDER=${AIDM_LLM_PROVIDER} requires an available Codex executable." >&2
    exit 127
  fi
  if [[ -z "${AIDM_CODEX_ACCESS_TOKEN:-${CODEX_ACCESS_TOKEN:-}}" ]]; then
    if [[ -z "${AIDM_CODEX_HOME:-}" || ! -f "${AIDM_CODEX_HOME}/auth.json" ]]; then
      echo "AIDM_LLM_PROVIDER=${AIDM_LLM_PROVIDER} requires a dedicated signed-in AIDM_CODEX_HOME or AIDM_CODEX_ACCESS_TOKEN." >&2
      exit 2
    fi
  fi
  CODEX_BIN_DIR="$(dirname "${RESOLVED_CODEX_EXECUTABLE}")"
  if [[ -x "${CODEX_BIN_DIR}/node" ]]; then
    export PATH="${CODEX_BIN_DIR}:${PATH}"
  fi
  export AIDM_CODEX_EXECUTABLE="${RESOLVED_CODEX_EXECUTABLE}"
fi

if [[ -n "${GUNICORN_BIN:-}" ]]; then
  GUNICORN_COMMAND=("${GUNICORN_BIN}")
elif "${RESOLVED_PYTHON_BIN}" -c 'import gunicorn' >/dev/null 2>&1; then
  GUNICORN_COMMAND=("${RESOLVED_PYTHON_BIN}" -m gunicorn)
else
  echo "gunicorn is not installed for ${RESOLVED_PYTHON_BIN}. Install runtime dependencies or set GUNICORN_BIN." >&2
  exit 127
fi

export AIDM_SOCKETIO_WORKER_MODEL="${AIDM_SOCKETIO_WORKER_MODEL:-single}"
export AIDM_SOCKETIO_ASYNC_MODE="${AIDM_SOCKETIO_ASYNC_MODE:-threading}"

if [[ "${AIDM_SOCKETIO_WORKER_MODEL}" != "single" ]]; then
  echo "scripts/run_production_server.sh currently supports only AIDM_SOCKETIO_WORKER_MODEL=single." >&2
  exit 2
fi

if [[ "${AIDM_SOCKETIO_ASYNC_MODE}" != "threading" ]]; then
  echo "AIDM_SOCKETIO_ASYNC_MODE must be threading for scripts/run_production_server.sh." >&2
  exit 2
fi

if ! [[ "${WEB_CONCURRENCY}" =~ ^[1-9][0-9]*$ ]]; then
  echo "WEB_CONCURRENCY must be a positive integer." >&2
  exit 2
fi

if ! [[ "${GUNICORN_THREADS}" =~ ^[1-9][0-9]*$ ]] || [[ "${GUNICORN_THREADS}" -lt 16 ]]; then
  echo "AIDM_GUNICORN_THREADS must be an integer >= 16." >&2
  exit 2
fi

if [[ "${WEB_CONCURRENCY}" != "1" ]]; then
  echo "AIDM_SOCKETIO_WORKER_MODEL=single requires WEB_CONCURRENCY=1." >&2
  exit 2
fi

cmd=(
  "${GUNICORN_COMMAND[@]}"
  --worker-class gthread
  --workers "${WEB_CONCURRENCY}"
  --threads "${GUNICORN_THREADS}"
  --bind "${BIND}"
  --timeout "${GUNICORN_TIMEOUT}"
  --access-logfile -
  --error-logfile -
  aidm_server.wsgi:app
)

if [[ "${1:-}" == "--print" ]]; then
  printf '%q ' "${cmd[@]}"
  printf '\n'
  exit 0
fi

cd "${ROOT_DIR}"
"${RESOLVED_PYTHON_BIN}" scripts/deploy_bootstrap.py --check-only --host "${PREFLIGHT_HOST}"

exec "${cmd[@]}"
