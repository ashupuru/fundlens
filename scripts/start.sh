#!/usr/bin/env bash
# Starts the backend (uvicorn, port 8000) and frontend (vite, port 5173) as
# background processes for local dev. PIDs and logs live in .run/ (gitignored).
# Safe to re-run: a service already running is left alone, not duplicated.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"
mkdir -p "$RUN_DIR"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_LOG="$RUN_DIR/backend.log"
FRONTEND_LOG="$RUN_DIR/frontend.log"

is_running() {
  [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null
}

if is_running "$BACKEND_PID_FILE"; then
  echo "Backend already running (PID $(cat "$BACKEND_PID_FILE"))"
else
  echo "Starting backend (uvicorn --reload, port 8000)..."
  (cd "$ROOT_DIR" && nohup uv run uvicorn fundlens.api.main:app --reload --port 8000 \
    > "$BACKEND_LOG" 2>&1 & echo $! > "$BACKEND_PID_FILE")
fi

if is_running "$FRONTEND_PID_FILE"; then
  echo "Frontend already running (PID $(cat "$FRONTEND_PID_FILE"))"
else
  echo "Starting frontend (vite, port 5173)..."
  (cd "$ROOT_DIR/frontend" && nohup pnpm run dev \
    > "$FRONTEND_LOG" 2>&1 & echo $! > "$FRONTEND_PID_FILE")
fi

sleep 2

echo
echo "Backend:  http://localhost:8000  (docs: http://localhost:8000/docs, logs: .run/backend.log)"
echo "Frontend: http://localhost:5173  (logs: .run/frontend.log)"
echo
echo "scripts/status.sh to check health, scripts/stop.sh to stop."
