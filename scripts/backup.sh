#!/usr/bin/env sh
set -eu

DB_PATH="${DB_PATH:-./data/repost.db}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/repost-$STAMP.db"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$OUT'"
else
  cp "$DB_PATH" "$OUT"
fi

echo "DB backup written: $OUT"
echo "Reminder: back up FERNET_KEY separately. Without it, encrypted tokens cannot be restored."
