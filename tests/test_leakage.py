"""LEAKAGE CANARY — must be green on every commit.

Pins: strict ts < asof, fixed-width timestamp monotonicity, and rejection of
naive datetimes at the boundary.
"""
import sqlite3
from datetime import datetime, timezone

import pytest

from core.db import store
from tests.helpers import q, dt


def test_read_asof_is_strict(conn):
    cid = "k:1"
    conn.execute("INSERT INTO contracts(contract_id,venue,match_id,family,outcome_side) "
                 "VALUES('k:1','kalshi',NULL,'series_winner','A')")
    store.upsert_quotes(conn, [
        q(cid, dt(2026, 7, 1, 12, 0, 0), mid=0.60),
        q(cid, dt(2026, 7, 1, 12, 5, 0), mid=0.62),
    ])
    # asof exactly at the second row's ts must EXCLUDE it (strict <)
    rows = store.read_asof(conn, cid, dt(2026, 7, 1, 12, 5, 0))
    assert len(rows) == 1
    assert rows[0]["mid"] == 0.60


def test_latest_asof_returns_gap_as_none(conn):
    assert store.latest_asof(conn, "missing", dt(2026, 7, 1)) is None


def test_timestamps_are_fixed_width_and_monotonic():
    a = store.to_ts(dt(2026, 7, 1, 9, 0, 0, 5))
    b = store.to_ts(dt(2026, 7, 1, 10, 0, 0, 0))
    assert len(a) == len(b)            # fixed width
    assert a < b                        # lexicographic == chronological
    assert store.from_ts(a) < store.from_ts(b)


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        store.to_ts(datetime(2026, 7, 1))  # naive
