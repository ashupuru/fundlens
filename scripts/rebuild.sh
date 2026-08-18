#!/usr/bin/env bash
# Rebuilds the local FundLens dev environment:
# - stops running backend/frontend services
# - refreshes Python and frontend dependencies if the tools are available
# - clears stale runtime state and local fallback key
# - restarts the app
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"
LOCAL_KEY="$ROOT_DIR/.fundlens_db_key"

mkdir -p "$RUN_DIR"

echo "==> Stopping existing services"
if [[ -f "$SCRIPT_DIR/stop.sh" ]]; then
  "$SCRIPT_DIR/stop.sh" || true
else
  echo "stop.sh not found; skipping"
fi

# Refresh Python dependencies if uv is available.
if command -v uv >/dev/null 2>&1; then
  echo "==> Syncing Python dependencies"
  (cd "$ROOT_DIR" && uv sync --extra dev)
else
  echo "==> uv not found; skipping Python dependency sync"
fi

# Refresh frontend dependencies if pnpm is available.
if command -v pnpm >/dev/null 2>&1; then
  echo "==> Installing frontend dependencies"
  (cd "$ROOT_DIR/frontend" && pnpm install --frozen-lockfile)
else
  echo "==> pnpm not found; skipping frontend dependency install"
fi

# Clear stale runtime artifacts and the local fallback key so the app starts fresh.
rm -rf "$RUN_DIR"
mkdir -p "$RUN_DIR"
rm -f "$LOCAL_KEY"

echo "==> Starting backend and frontend"
if [[ -f "$SCRIPT_DIR/start.sh" ]]; then
  "$SCRIPT_DIR/start.sh"
else
  echo "start.sh not found; cannot launch app"
  exit 1
fi

echo
printf 'Rebuild complete.\nCheck status with: %s/status.sh\n' "$SCRIPT_DIR"
