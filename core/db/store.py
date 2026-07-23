"""Point-in-time store. THE single source of as-of truth.

Every read that a signal, census, calibration, or lead-lag computation
performs must go through `read_asof` (or a helper here). No ad-hoc date
filters anywhere else. This module also owns timestamp encoding so that
lexicographic string order equals chronological order.

Invariants pinned by tests/test_leakage.py:
  * read_asof never returns a row with ts >= asof (strict <).
  * to_ts() produces fixed-width strings; ordering is monotonic in time.
  * naive datetimes are rejected at the boundary.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

_SCHEMA = Path(__file__).with_name("schema.sql")


# ---------------------------------------------------------------------------
# Timestamp encoding — fixed width, UTC, millisecond precision.
# ---------------------------------------------------------------------------
def to_ts(dt: datetime) -> str:
    """Encode an aware UTC datetime as fixed-width ISO-8601 (ms precision).

    Raises on naive datetimes — timezone discipline is enforced here, at
    the boundary, not sprinkled through call sites.
    """
    if dt.tzinfo is None:
        raise ValueError("naive datetime at store boundary; supply tzinfo=UTC")
    dt = dt.astimezone(timezone.utc)
    # millisecond precision, zero-padded, always the same width
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def from_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Connection / schema
# ---------------------------------------------------------------------------
def connect(db_path: str) -> sqlite3.Connection:
    if not db_path:
        raise ValueError("db_path required (each sport has its own DB: sport.params.db_path)")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA.read_text())
    conn.commit()


# ---------------------------------------------------------------------------
# THE as-of read. Strict ts < asof. Use nothing else to read time-series.
# ---------------------------------------------------------------------------
def read_asof(
    conn: sqlite3.Connection,
    contract_id: str,
    asof: datetime,
    *,
    source: str = "hist",
    limit: int | None = None,
) -> list[sqlite3.Row]:
    """Return quote rows for a contract strictly before `asof`, newest last.

    `source` selects provenance ('hist' | 'live'); mixing is never a
    default — callers that truly want both must query twice and combine
    explicitly.
    """
    asof_s = to_ts(asof)
    sql = (
        "SELECT * FROM quotes "
        "WHERE contract_id = ? AND source = ? AND ts < ? "
        "ORDER BY ts ASC"
    )
    params: list[object] = [contract_id, source, asof_s]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return list(conn.execute(sql, params))


def latest_asof(
    conn: sqlite3.Connection, contract_id: str, asof: datetime, *, source: str = "hist"
) -> sqlite3.Row | None:
    """The most recent quote strictly before `asof`, or None (a gap is a gap)."""
    rows = conn.execute(
        "SELECT * FROM quotes WHERE contract_id = ? AND source = ? AND ts < ? "
        "ORDER BY ts DESC LIMIT 1",
        [contract_id, source, to_ts(asof)],
    ).fetchone()
    return rows


def first_at_or_after(
    conn: sqlite3.Connection,
    contract_id: str,
    when: datetime,
    tolerance_s: int,
    *,
    source: str = "live",
) -> sqlite3.Row | None:
    """First quote at-or-after `when`, within tolerance. No lookahead:
    used for entry/checkpoint evaluation. Returns None if none exists yet
    (caller must wait or discard honestly, never grab an earlier quote)."""
    lo = to_ts(when)
    # tolerance handled by caller comparing timestamps; kept explicit for clarity
    row = conn.execute(
        "SELECT * FROM quotes WHERE contract_id = ? AND source = ? AND ts >= ? "
        "ORDER BY ts ASC LIMIT 1",
        [contract_id, source, lo],
    ).fetchone()
    if row is None:
        return None
    if (from_ts(row["ts"]) - when).total_seconds() > tolerance_s:
        return None
    return row


# ---------------------------------------------------------------------------
# Idempotent writes (natural keys + upsert).
# ---------------------------------------------------------------------------
def upsert_quotes(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    cols = (
        "contract_id", "venue", "ts", "source", "regime",
        "bid", "ask", "mid", "last", "bid_size_usd", "ask_size_usd",
    )
    payload = [tuple(r.get(c) for c in cols) for r in rows]
    conn.executemany(
        f"INSERT INTO quotes ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        "ON CONFLICT(contract_id, ts, source) DO UPDATE SET "
        "bid=excluded.bid, ask=excluded.ask, mid=excluded.mid, last=excluded.last, "
        "bid_size_usd=excluded.bid_size_usd, ask_size_usd=excluded.ask_size_usd, "
        "regime=excluded.regime",
        payload,
    )
    conn.commit()
    return len(payload)


def upsert_matches(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    cols = ("match_id", "league", "team_a", "team_b", "start_ts", "best_of",
            "neutral_source", "result_winner", "result_ts")
    payload = [tuple(r.get(c) for c in cols) for r in rows]
    conn.executemany(
        f"INSERT INTO matches ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        "ON CONFLICT(match_id) DO UPDATE SET "
        "league=excluded.league, team_a=excluded.team_a, team_b=excluded.team_b, "
        "start_ts=excluded.start_ts, best_of=excluded.best_of, "
        "neutral_source=excluded.neutral_source, result_winner=excluded.result_winner, "
        "result_ts=excluded.result_ts",
        payload,
    )
    conn.commit()
    return len(payload)


def upsert_contracts(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    cols = ("contract_id", "venue", "match_id", "family", "outcome_side",
            "question_text", "parity_ok")
    payload = [tuple(r.get(c) for c in cols) for r in rows]
    conn.executemany(
        f"INSERT INTO contracts ({','.join(cols)}) VALUES ({','.join('?' * len(cols))}) "
        "ON CONFLICT(contract_id) DO UPDATE SET "
        "match_id=excluded.match_id, family=excluded.family, "
        "outcome_side=excluded.outcome_side, question_text=excluded.question_text, "
        "parity_ok=excluded.parity_ok",
        payload,
    )
    conn.commit()
    return len(payload)


def upsert_book_snapshots_with_cursor(
    conn: sqlite3.Connection, rows: Iterable[dict], *,
    stream: str | None = None, cursor_value: str | None = None,
) -> int:
    """Idempotent upsert of book snapshots AND (optionally) the stream cursor,
    committed in ONE transaction. Restart safety: the cursor advances only if
    the rows it covers landed. Natural key (contract_id, ts, source) => a
    re-run over the same input changes nothing."""
    cols = ("contract_id", "venue", "ts", "source", "regime", "fetch_latency_ms",
            "best_bid", "best_ask", "mid", "top_bid_usd", "top_ask_usd",
            "full_bid_usd", "full_ask_usd", "n_levels", "book_ok", "raw_json")
    payload = [tuple(r.get(c) for c in cols) for r in rows]
    try:
        conn.execute("BEGIN")
        conn.executemany(
            f"INSERT INTO book_snapshots ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))}) "
            "ON CONFLICT(contract_id, ts, source) DO NOTHING",
            payload,
        )
        if stream is not None and cursor_value is not None:
            conn.execute(
                "INSERT INTO cursors (stream, cursor_value, updated_ts) VALUES (?,?,?) "
                "ON CONFLICT(stream) DO UPDATE SET cursor_value=excluded.cursor_value, "
                "updated_ts=excluded.updated_ts",
                [stream, cursor_value, to_ts(datetime.now(timezone.utc))],
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(payload)


def get_cursor(conn: sqlite3.Connection, stream: str) -> str | None:
    row = conn.execute("SELECT cursor_value FROM cursors WHERE stream = ?",
                       [stream]).fetchone()
    return row["cursor_value"] if row else None


def log_spend(conn: sqlite3.Connection, venue: str, endpoint: str, status: int,
              *, remaining_quota: str | None = None, note: str | None = None) -> None:
    conn.execute(
        "INSERT INTO spend_log (venue, ts, endpoint, status, remaining_quota, note) "
        "VALUES (?,?,?,?,?,?)",
        [venue, to_ts(datetime.now(timezone.utc)), endpoint, status, remaining_quota, note],
    )
    conn.commit()


def record_discard(
    conn: sqlite3.Connection, stage: str, reason: str, *,
    contract_id: str | None = None, match_id: str | None = None,
    when: datetime | None = None,
) -> None:
    conn.execute(
        "INSERT INTO discards (stage, contract_id, match_id, ts, reason) VALUES (?,?,?,?,?)",
        [stage, contract_id, match_id, to_ts(when or datetime.now(timezone.utc)), reason],
    )
    conn.commit()
