from core.db import store
from tests.helpers import q, dt


def test_upsert_is_idempotent(conn):
    conn.execute("INSERT INTO contracts(contract_id,venue,match_id,family,outcome_side) "
                 "VALUES('k:1','kalshi',NULL,'series_winner','A')")
    row = q("k:1", dt(2026, 7, 1, 12, 0, 0), mid=0.6)
    store.upsert_quotes(conn, [row])
    store.upsert_quotes(conn, [row])  # again, unchanged
    n = conn.execute("SELECT COUNT(*) c FROM quotes").fetchone()["c"]
    assert n == 1


def test_first_at_or_after_respects_tolerance(conn):
    conn.execute("INSERT INTO contracts(contract_id,venue,match_id,family,outcome_side) "
                 "VALUES('k:1','kalshi',NULL,'series_winner','A')")
    store.upsert_quotes(conn, [q("k:1", dt(2026, 7, 1, 12, 10, 0), source="live", mid=0.7)])
    # target 12:00, tolerance 60s -> the 12:10 quote is too far -> None
    assert store.first_at_or_after(conn, "k:1", dt(2026, 7, 1, 12, 0, 0), 60, source="live") is None
    # tolerance 900s -> found
    assert store.first_at_or_after(conn, "k:1", dt(2026, 7, 1, 12, 0, 0), 900, source="live") is not None
