#!/usr/bin/env bash
# Nightly SQLite backup. The VPS is disposable; the database is the project.
# Uses the online-backup API (safe while the recorder is writing).
set -euo pipefail
ROOT="/opt/pmarket-kalshi-research"
DB="$ROOT/data/db/pmk.db"
OUT="$ROOT/data/backups"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
sqlite3 "$DB" ".backup '$OUT/pmk-$STAMP.db'"
# keep 14 days on-box; pull copies OFF the box separately (rsync/object storage)
find "$OUT" -name 'pmk-*.db' -mtime +14 -delete
echo "backup ok: $OUT/pmk-$STAMP.db"
