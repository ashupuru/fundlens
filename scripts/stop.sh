#!/usr/bin/env bash
# Stops the backend/frontend dev processes started by scripts/start.sh.
# Killing just the captured top-level PID isn't reliably enough -- both
# `uv run uvicorn --reload` and `pnpm run dev` can spawn a multi-level child
# tree (reload worker / vite's own subprocess), and observed in practice that
# a plain `kill` on the top-level PID sometimes leaves a grandchild behind,
# still holding the port. Kill the whole descendant tree, deepest first.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"

# Prints $1 and every descendant PID, deepest first, so killing in that
# order never orphans a child before it's reached.
descendants_then_self() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    descendants_then_self "$child"
  done
  echo "$pid"
}

stop_service() {
  local name="$1" pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "$name: not running (no PID file)"
    return
  fi
  local pid
  pid="$(cat "$pid_file")"
  if kill -0 "$pid" 2>/dev/null; then
    for p in $(descendants_then_self "$pid"); do
      kill "$p" 2>/dev/null
    done
    echo "$name: stopped (PID $pid and its child processes)"
  else
    echo "$name: not running (stale PID file)"
  fi
  rm -f "$pid_file"
}

stop_service "Backend" "$RUN_DIR/backend.pid"
stop_service "Frontend" "$RUN_DIR/frontend.pid"
