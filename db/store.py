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

from config import CONFIG

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
def connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or CONFIG.db_path)
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
