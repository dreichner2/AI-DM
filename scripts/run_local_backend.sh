#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[run_local_backend] Missing virtualenv python at ${VENV_PYTHON}"
  echo "[run_local_backend] Run: make install (requires Python 3.14.6, Node.js 24.18.0, and npm 12.0.0)."
  exit 1
fi

if ! "${VENV_PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info[:3] == (3, 14, 6) else 1)'; then
  echo "[run_local_backend] Python 3.14.6 is required; run make install to recreate .venv."
  exit 1
fi

if [[ -f "${REPO_ROOT}/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.env.local"
  set +a
fi

if [[ -z "${AIDM_LLM_PROVIDER:-}" ]]; then
  if [[ -n "${GOOGLE_GENAI_API_KEY:-}" ]]; then
    export AIDM_LLM_PROVIDER="gemini"
  elif [[ -n "${AIDM_DEEPSEEK_API_KEY:-}" || -n "${DEEPSEEK_API_KEY:-}" ]]; then
    export AIDM_LLM_PROVIDER="deepseek"
  elif [[ -n "${AIDM_NVIDIA_API_KEY:-}" || -n "${NVIDIA_API_KEY:-}" ]]; then
    export AIDM_LLM_PROVIDER="nvidia"
  else
    export AIDM_LLM_PROVIDER="fallback"
  fi
fi

if [[ "${AIDM_LLM_PROVIDER}" == "deepseek" ]]; then
  export AIDM_LLM_MODEL="${AIDM_LLM_MODEL:-deepseek-v4-pro}"
  export AIDM_LLM_FALLBACK_MODELS="${AIDM_LLM_FALLBACK_MODELS:-}"
  export AIDM_DEEPSEEK_BASE_URL="${AIDM_DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
  export AIDM_DEEPSEEK_API_KEY="${AIDM_DEEPSEEK_API_KEY:-${DEEPSEEK_API_KEY:-}}"
elif [[ "${AIDM_LLM_PROVIDER}" == "nvidia" || "${AIDM_LLM_PROVIDER}" == "kimi" ]]; then
  export AIDM_LLM_MODEL="${AIDM_LLM_MODEL:-moonshotai/kimi-k2.5}"
  export AIDM_LLM_FALLBACK_MODELS="${AIDM_LLM_FALLBACK_MODELS:-}"
  export AIDM_NVIDIA_INVOKE_URL="${AIDM_NVIDIA_INVOKE_URL:-https://integrate.api.nvidia.com/v1}"
elif [[ "${AIDM_LLM_PROVIDER}" == "codex" || "${AIDM_LLM_PROVIDER}" == "codex_cli" ]]; then
  export AIDM_LLM_MODEL="${AIDM_LLM_MODEL:-gpt-5.6-sol-medium}"
  export AIDM_LLM_FALLBACK_MODELS="${AIDM_LLM_FALLBACK_MODELS:-}"
  export AIDM_CODEX_REASONING_EFFORT="${AIDM_CODEX_REASONING_EFFORT:-medium}"
  export AIDM_CODEX_TIMEOUT_SECONDS="${AIDM_CODEX_TIMEOUT_SECONDS:-240}"
elif [[ "${AIDM_LLM_PROVIDER}" == "fallback" ]]; then
  export AIDM_LLM_MODEL="${AIDM_LLM_MODEL:-deterministic-v1}"
  export AIDM_LLM_FALLBACK_MODELS="${AIDM_LLM_FALLBACK_MODELS:-}"
else
  export AIDM_LLM_MODEL="${AIDM_LLM_MODEL:-models/gemini-3-flash-preview}"
  export AIDM_LLM_FALLBACK_MODELS="${AIDM_LLM_FALLBACK_MODELS:-models/gemini-2.5-flash}"
fi
export AIDM_DEBUG="${AIDM_DEBUG:-false}"
if [[ "${AIDM_SERVE_FRONTEND:-false}" == "true" ]]; then
  export AIDM_CORS_ALLOWLIST="${AIDM_CORS_ALLOWLIST-}"
  export AIDM_SOCKET_CORS_ALLOWLIST="${AIDM_SOCKET_CORS_ALLOWLIST-${AIDM_CORS_ALLOWLIST}}"
else
  export AIDM_CORS_ALLOWLIST="${AIDM_CORS_ALLOWLIST:-*}"
  export AIDM_SOCKET_CORS_ALLOWLIST="${AIDM_SOCKET_CORS_ALLOWLIST:-*}"
fi
export AIDM_CORS_ALLOW_PRIVATE_NETWORK="${AIDM_CORS_ALLOW_PRIVATE_NETWORK:-true}"

PORT="${PORT:-5050}"

exec "${VENV_PYTHON}" "${REPO_ROOT}/scripts/deploy_bootstrap.py" --port "${PORT}" "$@"
