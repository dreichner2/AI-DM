#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_DIR="$(basename "$ROOT_DIR")"
PARENT_DIR="$(dirname "$ROOT_DIR")"
ARCHIVE_DIR="${1:-$ROOT_DIR/tmp/release}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE_PATH="$ARCHIVE_DIR/aidm-source-$STAMP.tar.gz"

mkdir -p "$ARCHIVE_DIR"

tar -czf "$ARCHIVE_PATH" -C "$PARENT_DIR" \
  --exclude "$BASE_DIR/.git" \
  --exclude "$BASE_DIR/.venv" \
  --exclude "$BASE_DIR/venv" \
  --exclude "$BASE_DIR/env" \
  --exclude "$BASE_DIR/.pytest_cache" \
  --exclude "$BASE_DIR/.ruff_cache" \
  --exclude "$BASE_DIR/.mypy_cache" \
  --exclude "$BASE_DIR/.playwright-cli" \
  --exclude "$BASE_DIR/.coverage" \
  --exclude "$BASE_DIR/htmlcov" \
  --exclude "$BASE_DIR/playwright-report" \
  --exclude "$BASE_DIR/test-results" \
  --exclude "$BASE_DIR/tmp" \
  --exclude "$BASE_DIR/.DS_Store" \
  --exclude "$BASE_DIR/**/.DS_Store" \
  --exclude "$BASE_DIR/.env" \
  --exclude "$BASE_DIR/.env.local" \
  --exclude "$BASE_DIR/*.log" \
  --exclude "$BASE_DIR/*.pid" \
  --exclude "$BASE_DIR/*.db" \
  --exclude "$BASE_DIR/*.sqlite" \
  --exclude "$BASE_DIR/*.sqlite3" \
  --exclude "$BASE_DIR/aidm_server/instance" \
  --exclude "$BASE_DIR/aidm_server/:memory:" \
  --exclude "$BASE_DIR/ai_dm/instance" \
  --exclude "$BASE_DIR/aidm_frontend/node_modules" \
  --exclude "$BASE_DIR/aidm_frontend/dist" \
  --exclude "$BASE_DIR/aidm_frontend/.vite" \
  --exclude "$BASE_DIR/aidm_frontend/tsconfig.tsbuildinfo" \
  --exclude "$BASE_DIR/**/__pycache__" \
  "$BASE_DIR"

echo "Created source archive: $ARCHIVE_PATH"
(
  cd "$ARCHIVE_DIR"
  shasum -a 256 "$(basename "$ARCHIVE_PATH")" > "$(basename "$ARCHIVE_PATH").sha256"
)
echo "SHA256: $(cut -d ' ' -f 1 "$ARCHIVE_PATH.sha256")"
du -sh "$ARCHIVE_PATH"
