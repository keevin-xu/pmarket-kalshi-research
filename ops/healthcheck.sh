#!/usr/bin/env bash
# Recorder watchdog. Answers one question: is capture actually happening?
#
# Written after two silent failures. On 2026-08-14 the disk filled and the
# recorder stopped persisting rows for FIVE DAYS while the service stayed
# "active" and the journal filled with warnings nobody read. Earlier, the
# recorder wrote snapshots with no contract metadata for three weeks. Both
# were found by hand, late. A service that is running is not a service that
# is working, so every check here reads the DATA, not the process.
#
#   usage: healthcheck.sh [--quiet]
#   cron:  */15 * * * * /opt/pmarket-kalshi-research/ops/healthcheck.sh --quiet
#
# Alerting is optional and credential-free. Put either or both in .env:
#   HEALTHCHECK_PING_URL=https://hc-ping.com/<uuid>   # dead-man's switch
#   NTFY_URL=https://ntfy.sh/<your-topic>             # push, for detail
#
# The dead-man's switch matters most: an alert that runs ON the failing box
# cannot tell you the box died. It is pinged ONLY when every check passes,
# so silence is itself the alarm.
set -uo pipefail

ROOT="${PMK_ROOT:-/opt/pmarket-kalshi-research}"
SPORTS="${PMK_SPORTS:-lol cs2}"
STALE_MIN="${PMK_STALE_MIN:-15}"        # newest row must be this fresh
BACKUP_MIN_MB="${PMK_BACKUP_MIN_MB:-10}"
DISK_WARN_PCT="${PMK_DISK_WARN_PCT:-85}"
QUIET=0; [ "${1:-}" = "--quiet" ] && QUIET=1

[ -f "$ROOT/.env" ] && . "$ROOT/.env" 2>/dev/null || true

problems=()
note() { [ "$QUIET" -eq 1 ] || echo "  $*"; }

sq() { sqlite3 -readonly "$1" "$2" 2>/dev/null; }

# GNU (server) vs BSD (a mac running this by hand) differ on stat/date.
_size()  { stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0; }
_mtime() { stat -c%Y "$1" 2>/dev/null || stat -f%m "$1" 2>/dev/null || echo 0; }
_epoch() {  # ISO-8601 UTC -> unix seconds
    date -u -d "$1" +%s 2>/dev/null \
      || date -u -j -f "%Y-%m-%dT%H:%M:%S" "${1%%.*}" +%s 2>/dev/null \
      || echo 0
}

for s in $SPORTS; do
    db="$ROOT/data/$s/db/pmk.db"
    if [ ! -r "$db" ]; then
        problems+=("$s: database unreadable at $db")
        continue
    fi

    # 1. Is the recorder still WRITING? The check that would have caught the
    #    five-day outage on day one.
    newest="$(sq "$db" "SELECT MAX(ts) FROM book_snapshots;")"
    if [ -z "$newest" ]; then
        problems+=("$s: no book_snapshots at all")
    else
        age=$(( ( $(date -u +%s) - $(_epoch "${newest%.*}") ) / 60 ))
        note "$s newest row: $newest (${age}m old)"
        if [ "$age" -gt "$STALE_MIN" ]; then
            problems+=("$s: STALE — newest row is ${age}m old (limit ${STALE_MIN}m)")
        fi
    fi

    # 2. Did last night's backup produce a real file? Four consecutive nights
    #    wrote ZERO-byte backups while reporting success.
    newest_bk="$(ls -t "$ROOT/data/$s/backups/"pmk-*.db 2>/dev/null | head -1)"
    if [ -z "$newest_bk" ]; then
        problems+=("$s: no backup file present")
    else
        mb=$(( $(_size "$newest_bk") / 1048576 ))
        age_h=$(( ( $(date -u +%s) - $(_mtime "$newest_bk") ) / 3600 ))
        note "$s newest backup: $(basename "$newest_bk") ${mb}MB ${age_h}h old"
        [ "$mb" -lt "$BACKUP_MIN_MB" ] && problems+=("$s: backup is ${mb}MB — expected >= ${BACKUP_MIN_MB}MB")
        [ "$age_h" -gt 48 ] && problems+=("$s: newest backup is ${age_h}h old")
    fi

    # 3. Are recorded books still mappable? A snapshot with no contract row is
    #    an opaque id, and the venue stops serving the market at ~68 days.
    unmapped="$(sq "$db" "SELECT COUNT(*) FROM (SELECT DISTINCT b.contract_id
        FROM book_snapshots b LEFT JOIN contracts c ON c.contract_id=b.contract_id
        WHERE c.contract_id IS NULL AND b.ts > datetime('now','-2 days'));")"
    if [ -n "$unmapped" ] && [ "$unmapped" -gt 50 ]; then
        problems+=("$s: $unmapped recently-recorded contracts have no metadata")
    fi
done

# 4. Disk, on every filesystem the data actually lives on (db/backups may be
#    symlinked onto a volume, so check the resolved path, not just /).
for path in / "$ROOT/data" $(for s in $SPORTS; do readlink -f "$ROOT/data/$s/db"; done); do
    pct="$(df -P "$path" 2>/dev/null | tail -1 | awk '{print $5}' | tr -dc '0-9')"
    [ -z "$pct" ] && continue
    note "disk $path: ${pct}%"
    [ "$pct" -ge "$DISK_WARN_PCT" ] && problems+=("disk ${path} at ${pct}% (limit ${DISK_WARN_PCT}%)")
done

# 5. Degradation the recorder reports itself but nobody reads.
for s in $SPORTS; do
    hits="$(journalctl -u "pmk-recorder@$s" --since "1 hour ago" 2>/dev/null \
            | grep -cE 'DISK-GUARD|CIRCUIT-BREAK' || true)"
    [ "${hits:-0}" -gt 0 ] && problems+=("$s: $hits DISK-GUARD/CIRCUIT-BREAK in the last hour")
done

if [ "${#problems[@]}" -eq 0 ]; then
    [ "$QUIET" -eq 1 ] || echo "OK — all checks passed"
    # Ping ONLY on success: silence is the alarm.
    [ -n "${HEALTHCHECK_PING_URL:-}" ] && curl -fsS -m 10 "$HEALTHCHECK_PING_URL" >/dev/null
    exit 0
fi

msg="pmk recorder ALERT on $(hostname):"$'\n'"$(printf '  - %s\n' "${problems[@]}")"
echo "$msg" >&2
[ -n "${NTFY_URL:-}" ] && curl -fsS -m 10 -H "Title: pmk recorder alert" \
    -H "Priority: high" -d "$msg" "$NTFY_URL" >/dev/null
# Tell the dead-man's switch this run FAILED, so it alerts immediately rather
# than waiting for the grace period to lapse.
[ -n "${HEALTHCHECK_PING_URL:-}" ] && curl -fsS -m 10 --data-raw "$msg" \
    "${HEALTHCHECK_PING_URL}/fail" >/dev/null
exit 1
