#!/usr/bin/env bash
# Reports whether the backend/frontend dev processes started by
# scripts/start.sh are running, plus a live health check for the backend.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"

check_service() {
  local name="$1" pid_file="$2"
  if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
    echo "$name: running (PID $(cat "$pid_file"))"
  else
    echo "$name: not running"
  fi
}

check_service "Backend " "$RUN_DIR/backend.pid"
check_service "Frontend" "$RUN_DIR/frontend.pid"

echo
if curl -s -o /dev/null --max-time 2 http://localhost:8000/health; then
  echo "Backend health check: OK (http://localhost:8000/health)"
else
  echo "Backend health check: unreachable"
fi
