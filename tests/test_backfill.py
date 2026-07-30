"""History capture. Adapters are stubs — no test touches a vendor.

The properties that matter here are integrity ones: a refusal must never be
recorded as "no history", an empty series must leave a reason code rather
than a silent hole, re-runs must not re-fetch or duplicate, and prices must
land as order-book mid (Kalshi) / traded last (Polymarket) with NO de-vig.
"""
import pytest

from core.config import CONFIG
from core.db import store
from core.ingest import backfill as backfill_mod
from core.ingest.backfill import (Backfill, kalshi_candle_rows,
                                  polymarket_history_rows)
from core.ingest.base import VendorError
from sports.cs2 import Cs2Sport

CANDLES = [
    {"end_period_ts": 1785380000, "yes_bid": {"close_dollars": "0.40"},
     "yes_ask": {"close_dollars": "0.44"}, "price": {"previous_dollars": "0.42"}},
    {"end_period_ts": 1785380060, "yes_bid": {"close_dollars": "0.00"},
     "yes_ask": {"close_dollars": "0.00"}},                      # no two-sided price
    {"end_period_ts": 1785380120, "yes_bid": {"close_dollars": "0.61"},
     "yes_ask": {"close_dollars": "0.63"}, "price": {}},
]

EVENT_PAGE = {
    "cursor": None,
    "events": [{
        "event_ticker": "KXCS2MAP-26JUL301800IOWLAG-1",
        "title": "Iowa Stormboar vs. LAG: Map 1",
        "markets": [
            {"ticker": "KXCS2MAP-26JUL301800IOWLAG-1-IOW", "yes_sub_title": "Iowa Stormboar",
             "open_time": "2026-07-30T01:00:00Z", "close_time": "2026-07-30T03:00:00Z",
             "result": "no"},
            {"ticker": "KXCS2MAP-26JUL301800IOWLAG-1-LAG", "yes_sub_title": "LAG",
             "open_time": "2026-07-30T01:00:00Z", "close_time": "2026-07-30T03:00:00Z",
             "result": "yes"},
        ],
    }],
}

PM_EVENT = {
    "slug": "cs2-iow-lag-2026-07-30",
    "title": "Counter-Strike: Iowa Stormboar vs LAG (BO3) - IEM Cologne Major",
    "markets": [
        {"conditionId": "0xmap1", "question": "Counter-Strike: Iowa Stormboar vs LAG - Map 1 Winner",
         "outcomes": '["Iowa Stormboar", "LAG"]', "clobTokenIds": '["tok-a", "tok-b"]'},
        {"conditionId": "0xprop", "question": "Map 1 Total Rounds: Over/Under 21.5",
         "outcomes": '["Over", "Under"]', "clobTokenIds": '["tok-c", "tok-d"]'},
    ],
}

HISTORY = [{"t": 1785380000, "p": 0.48}, {"t": 1785380600, "p": 0.61}]


class FakeKalshi:
    venue = CONFIG.venues.KALSHI

    def __init__(self, candles=None, fail=None):
        self.candles = CANDLES if candles is None else candles
        self.fail = fail
        self.candle_calls = []

    def list_events(self, series, *, status=None, cursor=None):
        return EVENT_PAGE if series == "KXCS2MAP" else {"events": [], "cursor": None}

    def candlesticks(self, series, ticker, start_ts, end_ts, period_min=1):
        self.candle_calls.append(ticker)
        if self.fail:
            raise self.fail
        return self.candles


class FakePoly:
    venue = CONFIG.venues.POLYMARKET

    def __init__(self, history=None):
        self.history = HISTORY if history is None else history
        self.history_calls = []

    def iter_events(self, tag, *, closed=None, stop_before=None):
        yield PM_EVENT

    def prices_history(self, token, *, fidelity=1):
        self.history_calls.append(token)
        return self.history


@pytest.fixture
def bf(conn):
    sport = Cs2Sport()
    b = Backfill(conn, sport, kalshi=FakeKalshi(), poly=FakePoly())
    b._neutral_idx = {}          # no neutral archive in tests; scope='all' is used
    store.init_schema(conn)
    return b


def test_kalshi_candles_become_mid_rows_with_no_devig():
    rows = kalshi_candle_rows("T", CANDLES)
    assert len(rows) == 2                       # the un-priced candle is dropped, not zeroed
    assert rows[0]["mid"] == pytest.approx(0.42)
    assert rows[0]["bid"] == 0.40 and rows[0]["ask"] == 0.44
    assert rows[0]["source"] == "hist" and rows[0]["venue"] == "kalshi"
    assert rows[0]["ts"] == "2026-07-30T02:53:20.000Z"      # unix -> fixed-width UTC
    assert rows[0]["last"] == pytest.approx(0.42) and rows[1]["last"] is None
    assert all(r["bid_size_usd"] is None for r in rows)     # candles carry no depth


def test_polymarket_history_lands_in_last_not_mid():
    rows = polymarket_history_rows("0xabc", HISTORY)
    assert [r["last"] for r in rows] == [0.48, 0.61]
    assert all(r["mid"] is None and r["bid"] is None for r in rows)
    assert all(r["source"] == "hist" for r in rows)


def test_kalshi_stage_writes_contracts_then_quotes(bf, conn):
    out = bf.kalshi_history(scope="all")
    assert out["events"] == 1 and out["quote_rows"] == 4      # 2 markets x 2 candles
    fams = dict(conn.execute("SELECT contract_id, family FROM contracts").fetchall())
    assert set(fams.values()) == {"map_winner"}
    assert conn.execute("SELECT COUNT(*) FROM quotes WHERE source='hist'").fetchone()[0] == 4


def test_polymarket_stage_skips_props_and_keeps_one_series_per_market(bf, conn):
    out = bf.polymarket_history(scope="all")
    assert out["events"] == 1
    rows = conn.execute("SELECT contract_id, family, outcome_side FROM contracts").fetchall()
    assert [r[0] for r in rows] == ["0xmap1"]                 # the prop never enters
    assert rows[0][2] == "Iowa Stormboar"                     # which side the price means
    assert bf.poly.history_calls == ["tok-a"]                 # binary book: one leg suffices
    assert out["quote_rows"] == 2


def test_rerun_is_idempotent_and_does_not_refetch(bf, conn):
    bf.kalshi_history(scope="all")
    before = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    calls = len(bf.kalshi.candle_calls)
    out = bf.kalshi_history(scope="all")
    assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == before
    assert len(bf.kalshi.candle_calls) == calls               # already captured -> skipped
    assert out["already_captured"] == 2


def test_an_empty_series_is_a_recorded_gap_not_a_silent_hole(conn):
    sport = Cs2Sport()
    b = Backfill(conn, sport, kalshi=FakeKalshi(candles=[]), poly=FakePoly(history=[]))
    b._neutral_idx = {}
    store.init_schema(conn)
    b.kalshi_history(scope="all")
    b.polymarket_history(scope="all")
    reasons = [r[0] for r in conn.execute(
        "SELECT reason FROM discards WHERE stage='backfill'").fetchall()]
    assert "empty_candles" in reasons and "history_evaporated" in reasons


def test_a_refusal_is_never_recorded_as_no_history(conn, monkeypatch):
    """429/5xx must fail the stage loudly. Writing 'no rows' here would put a
    vendor outage into the archive as though nothing had traded."""
    monkeypatch.setattr(backfill_mod.time, "sleep", lambda _s: None)
    sport = Cs2Sport()
    b = Backfill(conn, sport, kalshi=FakeKalshi(fail=VendorError("429", status=429)),
                 poly=FakePoly())
    b._neutral_idx = {}
    store.init_schema(conn)
    with pytest.raises(VendorError):
        b.kalshi_history(scope="all")
    assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0


def test_a_per_item_gap_is_skipped_not_fatal(conn):
    sport = Cs2Sport()
    b = Backfill(conn, sport, kalshi=FakeKalshi(fail=VendorError("404", status=404)),
                 poly=FakePoly())
    b._neutral_idx = {}
    store.init_schema(conn)
    out = b.kalshi_history(scope="all")
    assert out["quote_rows"] == 0
    assert conn.execute("SELECT COUNT(*) FROM discards WHERE reason='no_candles'"
                        ).fetchone()[0] == 2


def test_dry_run_touches_nothing(conn):
    sport = Cs2Sport()
    b = Backfill(conn, sport, kalshi=FakeKalshi(), poly=FakePoly(), dry_run=True)
    b._neutral_idx = {}
    store.init_schema(conn)
    out = b.run(stages=("kalshi", "polymarket"), scope="all")
    assert out["kalshi"]["candle_calls_planned"] == 2
    assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM contracts").fetchone()[0] == 0
    assert bf_calls(b) == 0


def bf_calls(b) -> int:
    return len(b.kalshi.candle_calls) + len(b.poly.history_calls)
