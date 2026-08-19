#!/usr/bin/env bash
# Timestamped copy of the configured FundLens database.
#
# Run before anything destructive (duplicate cleanup, migrations you're unsure
# of). SQLite is a single file, so a copy is a complete, restorable backup --
# there's no WAL to reconcile here because this app uses the default journal
# mode and closes its connections between requests.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Mirrors settings.py: FUNDLENS_DB_PATH wins, else ./fundlens.db. Read from
# .env the same way pydantic-settings does, so the backup can never target a
# different database than the app.
DB_PATH="${FUNDLENS_DB_PATH:-}"
if [[ -z "$DB_PATH" && -f .env ]]; then
  DB_PATH="$(grep -E '^FUNDLENS_DB_PATH=' .env | tail -1 | cut -d= -f2- || true)"
fi
DB_PATH="${DB_PATH:-./fundlens.db}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "No database at $DB_PATH — nothing to back up." >&2
  exit 1
fi

BACKUP_DIR="$ROOT_DIR/.backups"
mkdir -p "$BACKUP_DIR"
DEST="$BACKUP_DIR/$(basename "$DB_PATH" .db)-$(date +%Y%m%d-%H%M%S).db"

cp "$DB_PATH" "$DEST"
echo "Backed up $DB_PATH"
echo "        -> $DEST ($(du -h "$DEST" | cut -f1))"
