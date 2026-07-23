"""Recorder integrity tests (Field Guide checklist). No live vendor: adapters
are fakes. Covers book parsing (worst-first, gaps), restart-safe idempotent
ingest, atomic cursor, and the 429 circuit breaker."""
from __future__ import annotations

import pytest

from db import store
from ingest.base import VendorError
from ingest import record


@pytest.fixture
def conn(tmp_path):
    c = store.connect(str(tmp_path / "rec.db"))
    store.init_schema(c)
    return c


# --- book parsing -------------------------------------------------------------
def test_polymarket_full_book_and_worst_first():
    raw = {"bids": [{"price": "0.40", "size": "100"}, {"price": "0.55", "size": "200"}],
           "asks": [{"price": "0.70", "size": "50"}, {"price": "0.60", "size": "80"}]}
    s = record.parse_polymarket_book(raw, "0xabc", "in_game", 12, "2026-07-23T08:00:00.000Z")
    assert s["best_bid"] == 0.55 and s["best_ask"] == 0.60      # max bid / min ask
    assert s["top_bid_usd"] == 110.0 and s["top_ask_usd"] == 48.0
    assert s["full_bid_usd"] == 150.0                            # 0.40*100 + 0.55*200
    assert s["full_ask_usd"] == 83.0 and s["book_ok"] == 1


def test_one_sided_book_is_gap_not_zero():
    s = record.parse_polymarket_book({"bids": [{"price": "0.5", "size": "10"}], "asks": []},
                                     "0x", None, 5, "2026-07-23T08:00:00.000Z")
    assert s["best_ask"] is None and s["top_ask_usd"] is None    # gap, never 0
    assert s["book_ok"] == 0


def test_kalshi_top_of_book_from_market_fields():
    mkt = {"ticker": "KXLOLMAP-X-1-GENG", "yes_bid_dollars": 0.60, "yes_ask_dollars": 0.64,
           "yes_bid_size_fp": 1000, "yes_ask_size_fp": 500}
    s = record.parse_kalshi_market(mkt, {"orderbook_fp": {}}, "pre_match", 8,
                                   "2026-07-23T08:00:00.000Z")
    assert s["mid"] == 0.62 and s["book_ok"] == 1
    assert s["top_bid_usd"] == 600.0 and s["top_ask_usd"] == 320.0


# --- restart safety / idempotence --------------------------------------------
def test_double_ingest_is_a_no_op(conn):
    row = record.parse_polymarket_book(
        {"bids": [{"price": "0.5", "size": "10"}], "asks": [{"price": "0.6", "size": "10"}]},
        "0x", "in_game", 1, "2026-07-23T08:00:00.000Z")
    store.upsert_book_snapshots_with_cursor(conn, [row], stream="s", cursor_value="c1")
    store.upsert_book_snapshots_with_cursor(conn, [row], stream="s", cursor_value="c2")
    n = conn.execute("SELECT COUNT(*) FROM book_snapshots").fetchone()[0]
    assert n == 1                                    # same natural key => one row
    assert store.get_cursor(conn, "s") == "c2"       # cursor still advanced


# --- fakes for the cycle ------------------------------------------------------
class _FakePoly:
    venue = "polymarket"

    def __init__(self, raise_book=False, book_status=429):
        self.raise_book = raise_book
        self.book_status = book_status

    def iter_events(self, *, closed=None):
        return [{"title": "LoL: Gen.G vs T1 (BO3) - LCK Round 3-4",
                 "slug": "lol-geng-t1-2026-07-23",
                 "startDate": "2026-07-23T08:00:00Z",
                 "markets": [{"question": "LoL: Gen.G vs T1 - Game 1 Winner",
                              "conditionId": "0xabc", "clobTokenIds": ["tok1", "tok2"]}]}]

    def book(self, token):
        if self.raise_book:
            raise VendorError("boom", status=self.book_status)
        return {"bids": [{"price": "0.5", "size": "100"}],
                "asks": [{"price": "0.6", "size": "100"}]}


class _FakeKalshi:
    venue = "kalshi"

    def list_events(self, series, status=None, cursor=None):
        if series == "KXLOLMAP":
            return {"events": [{"markets": [
                {"ticker": "KXLOLMAP-X-1-GENG", "status": "active",
                 "yes_bid_dollars": 0.6, "yes_ask_dollars": 0.64,
                 "yes_bid_size_fp": 100, "yes_ask_size_fp": 100,
                 "close_time": "2026-07-23T09:00:00Z"}]}]}
        return {"events": []}

    def get_orderbook(self, ticker):
        return {"orderbook_fp": {}}


def test_poll_cycle_writes_both_venues(conn):
    n = record.Recorder(conn, _FakeKalshi(), _FakePoly()).poll_cycle()
    assert n == 2
    venues = {r[0] for r in conn.execute("SELECT DISTINCT venue FROM book_snapshots")}
    assert venues == {"polymarket", "kalshi"}
    src = conn.execute("SELECT DISTINCT source FROM book_snapshots").fetchone()[0]
    assert src == "live"


def test_circuit_breaker_trips_on_429(conn):
    rec = record.Recorder(conn, _FakeKalshi(), _FakePoly(raise_book=True, book_status=429))
    rec.poll_cycle()
    assert rec._blocked("polymarket")            # 429 armed the cooldown
    assert not rec._blocked("kalshi")            # other venue unaffected
    kn = conn.execute("SELECT COUNT(*) FROM book_snapshots WHERE venue='kalshi'").fetchone()[0]
    assert kn == 1                               # Kalshi still recorded


def test_404_is_a_gap_not_a_trip(conn):
    # a per-market 404 must skip that market, NOT circuit-break the venue
    rec = record.Recorder(conn, _FakeKalshi(), _FakePoly(raise_book=True, book_status=404))
    rec.poll_cycle()
    assert not rec._blocked("polymarket")
