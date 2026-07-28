#!/usr/bin/env bash
# Nightly per-sport SQLite backup. The VPS is disposable; the database is the
# project. Uses the online-backup API (safe while the recorder is writing).
#   usage: backup.sh [sport]   (default: lol)
set -euo pipefail
ROOT="/opt/pmarket-kalshi-research"
SPORT="${1:-lol}"
DB="$ROOT/data/$SPORT/db/pmk.db"
OUT="$ROOT/data/$SPORT/backups"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
sqlite3 "$DB" ".backup '$OUT/pmk-$STAMP.db'"
# keep only 3 days on-box (the DB grows; full copies add up) — pull copies OFF
# the box for real insurance (rsync/object storage), see ops/RECORDER_OPS.md
find "$OUT" -name 'pmk-*.db' -mtime +3 -delete
echo "backup ok: $OUT/pmk-$STAMP.db"
