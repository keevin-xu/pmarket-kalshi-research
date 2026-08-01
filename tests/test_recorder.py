"""Recorder integrity tests (Field Guide checklist). No live vendor: adapters
are fakes. Covers book parsing (worst-first, gaps), restart-safe idempotent
ingest, atomic cursor, and the 429 circuit breaker."""
from __future__ import annotations

import pytest

from dataclasses import replace

from core.config import CONFIG
from core.db import store
from core.ingest.base import VendorError
from core.ingest import record
from sports.lol import LolSport

SPORT = LolSport()


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


def test_compact_ladder_preserves_full_levels():
    import json
    raw = {"bids": [{"price": "0.40", "size": "100"}, {"price": "0.55", "size": "200"}],
           "asks": [{"price": "0.60", "size": "80"}]}
    # compact (default) keeps EVERY level as [price, size] -> price-impact curve intact
    s = record.parse_polymarket_book(raw, "0x", "in_game", 1, "2026-07-28T00:00:00.000Z")
    lad = json.loads(s["raw_json"])
    assert lad["bids"] == [[0.4, 100.0], [0.55, 200.0]] and lad["asks"] == [[0.6, 80.0]]
    # 'none' drops the ladder but parsed depth columns still record
    s2 = record.parse_polymarket_book(raw, "0x", "in_game", 1, "2026-07-28T00:00:00.000Z",
                                      archive="none")
    assert s2["raw_json"] is None and s2["full_bid_usd"] == 150.0


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

    def iter_events(self, tag, *, closed=None):
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
    n = record.Recorder(conn, SPORT, _FakeKalshi(), _FakePoly()).poll_cycle()
    assert n == 2
    venues = {r[0] for r in conn.execute("SELECT DISTINCT venue FROM book_snapshots")}
    assert venues == {"polymarket", "kalshi"}
    src = conn.execute("SELECT DISTINCT source FROM book_snapshots").fetchone()[0]
    assert src == "live"


def test_circuit_breaker_trips_on_429(conn):
    rec = record.Recorder(conn, SPORT, _FakeKalshi(), _FakePoly(raise_book=True, book_status=429))
    rec.poll_cycle()
    assert rec._blocked("polymarket")            # 429 armed the cooldown
    assert not rec._blocked("kalshi")            # other venue unaffected
    kn = conn.execute("SELECT COUNT(*) FROM book_snapshots WHERE venue='kalshi'").fetchone()[0]
    assert kn == 1                               # Kalshi still recorded


def test_404_is_a_gap_not_a_trip(conn):
    # a per-market 404 must skip that market, NOT circuit-break the venue
    rec = record.Recorder(conn, SPORT, _FakeKalshi(), _FakePoly(raise_book=True, book_status=404))
    rec.poll_cycle()
    assert not rec._blocked("polymarket")


# --- cap rotation + no-book resting -------------------------------------------
def _tune(monkeypatch, **kw):
    """RecorderConfig is frozen (as it should be); swap in a tuned copy."""
    monkeypatch.setattr(record, "CONFIG",
                        replace(CONFIG, recorder=replace(CONFIG.recorder, **kw)))
class _ManyPoly(_FakePoly):
    """A catalog larger than the cap, where most markets have no book — the
    real CS2 shape (728 fixtures, ~75% returning 404)."""

    def __init__(self, n=7, with_book=(0, 1)):
        super().__init__()
        self.n = n
        self.with_book = set(with_book)
        self.polled: list[str] = []

    def iter_events(self, tag, *, closed=None, stop_before=None):
        return [{"title": f"LoL: A{i} vs B{i} (BO3) - LCK", "slug": f"lol-a-b-2026-07-2{i%10}",
                 "markets": [{"question": "LoL: A vs B - Game 1 Winner",
                              "conditionId": f"0x{i}", "clobTokenIds": [f"tok{i}", "t2"]}]}
                for i in range(self.n)]

    def book(self, token):
        self.polled.append(token)
        idx = int(token.replace("tok", ""))
        if idx not in self.with_book:
            raise VendorError("no book", status=404)
        return {"bids": [{"price": "0.5", "size": "10"}], "asks": [{"price": "0.6", "size": "10"}]}


def test_cap_rotates_so_the_tail_is_never_permanently_blind(conn, monkeypatch):
    """A fixed head-slice would poll markets 0-2 forever and never see 3-6.
    Every market must be visited within ceil(len/cap) cycles."""
    _tune(monkeypatch, max_markets_per_cycle=3)
    poly = _ManyPoly(n=7, with_book=range(7))
    rec = record.Recorder(conn, SPORT, _FakeKalshi(), poly)
    for _ in range(3):                       # ceil(7/3) = 3 cycles
        rec._catalog = None                  # force re-discovery each cycle
        rec.poll_cycle()
    assert {t for t in poly.polled} == {f"tok{i}" for i in range(7)}


def test_a_market_with_no_book_rests_instead_of_burning_the_budget(conn):
    poly = _ManyPoly(n=4, with_book=(0,))
    rec = record.Recorder(conn, SPORT, _FakeKalshi(), poly)
    rec.poll_cycle()
    first = len(poly.polled)
    poly.polled.clear()
    rec._catalog = None
    rec.poll_cycle()
    # only the one market that HAS a book is polled again; the 404s are resting
    assert first == 4 and poly.polled == ["tok0"]


def test_resting_expires_so_a_market_that_gains_a_book_is_picked_up(conn, monkeypatch):
    _tune(monkeypatch, nobook_cooldown_cycles=2)
    poly = _ManyPoly(n=2, with_book=(0,))
    rec = record.Recorder(conn, SPORT, _FakeKalshi(), poly)
    for _ in range(4):
        rec._catalog = None
        rec.poll_cycle()
    assert poly.polled.count("tok1") >= 2     # retried after the cooldown, not dropped forever


def test_kalshi_full_book_uses_the_real_schema_and_derives_the_ask_side():
    """Schema pinned from a live CS2 book: two BID ladders, string-valued.
    The parser previously looked for 'yes'/'no' keys that do not exist, so
    every recorded row carried NULL full-book depth and an empty ladder."""
    mkt = {"ticker": "KXCS2MAP-X-1-MAR", "yes_bid_dollars": 0.66,
           "yes_ask_dollars": 0.70, "yes_bid_size_fp": 82, "yes_ask_size_fp": 198}
    raw = {"orderbook_fp": {
        "yes_dollars": [["0.6500", "100.00"], ["0.6600", "82.00"]],
        "no_dollars": [["0.2900", "198.00"], ["0.3000", "123.00"]]}}
    s = record.parse_kalshi_market(mkt, raw, "in_game", 9, "2026-08-01T00:00:00.000Z")
    assert s["n_levels"] == 4                       # not None, and not zero
    assert s["full_bid_usd"] == round(0.65 * 100 + 0.66 * 82, 2)
    # NO bids at 0.29/0.30 are YES asks at 0.71/0.70
    assert s["full_ask_usd"] == round(0.71 * 198 + 0.70 * 123, 2)
    import json
    lad = json.loads(s["raw_json"])
    assert lad["yes"] and lad["no"]                 # the ladder is actually archived


def test_unrecognised_book_stays_a_gap_not_a_zero():
    mkt = {"ticker": "T", "yes_bid_dollars": 0.5, "yes_ask_dollars": 0.6,
           "yes_bid_size_fp": 10, "yes_ask_size_fp": 10}
    s = record.parse_kalshi_market(mkt, {"orderbook_fp": {}}, None, 1,
                                   "2026-08-01T00:00:00.000Z")
    assert s["full_bid_usd"] is None and s["n_levels"] is None
