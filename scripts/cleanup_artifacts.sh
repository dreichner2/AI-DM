#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

rm -rf \
  "$ROOT_DIR/.pytest_cache" \
  "$ROOT_DIR/aidm_server/:memory:" \
  "$ROOT_DIR/aidm_frontend/.vite" \
  "$ROOT_DIR/aidm_frontend/dist"

if [[ -d "$ROOT_DIR/tmp" ]]; then
  find "$ROOT_DIR/tmp" -mindepth 1 -maxdepth 1 ! -name "release" -exec rm -rf {} +
fi

find "$ROOT_DIR" \
  -path "$ROOT_DIR/.git" -prune -o \
  -path "$ROOT_DIR/.venv" -prune -o \
  -path "$ROOT_DIR/aidm_frontend/node_modules" -prune -o \
  -type d -name "__pycache__" -prune -exec rm -rf {} +

find "$ROOT_DIR" \
  -path "$ROOT_DIR/.git" -prune -o \
  -path "$ROOT_DIR/.venv" -prune -o \
  -path "$ROOT_DIR/aidm_frontend/node_modules" -prune -o \
  -type f -name ".DS_Store" -delete

echo "Cleaned local cache and runtime artifacts."
