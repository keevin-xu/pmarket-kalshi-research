"""Calibration snapshot helpers: no-lookahead price-at-target, from tiny
synthetic series. Order-book mid, NO de-vig."""
from core.ingest.kalshi import KalshiAdapter
from core.reference.calib_data import _pm_price_at


def _candle(ts, bid, ask):
    return {"end_period_ts": ts, "yes_bid": {"close_dollars": str(bid)},
            "yes_ask": {"close_dollars": str(ask)}}


def test_kalshi_candle_mid_at_no_lookahead():
    candles = [_candle(100, 0.40, 0.44), _candle(160, 0.50, 0.54),
               _candle(220, 0.90, 0.94)]
    # target between candle 2 and 3 -> uses candle at 160, NOT the later 220
    assert KalshiAdapter.candle_mid_at(candles, 200) == 0.52
    assert KalshiAdapter.candle_mid_at(candles, 100) == 0.42  # exact-at boundary
    assert KalshiAdapter.candle_mid_at(candles, 50) is None    # nothing before yet


def test_kalshi_candle_mid_one_sided_is_gap():
    assert KalshiAdapter.candle_mid_at([_candle(100, 0, 0)], 150) is None


def test_pm_price_at_no_lookahead():
    hist = [{"t": 100, "p": 0.30}, {"t": 160, "p": 0.55}, {"t": 220, "p": 0.95}]
    assert _pm_price_at(hist, 200) == 0.55   # last at-or-before, not the 220 tick
    assert _pm_price_at(hist, 90) is None


# --- store-backed reads (gates must not re-fetch from vendors) ---------------
def test_price_series_and_price_at_respect_gaps_and_no_lookahead(conn, utc):
    from core.db import store
    store.upsert_contracts(conn, [{"contract_id": "K1", "venue": "kalshi",
                                   "family": "map_winner", "outcome_side": "A"}])
    store.upsert_quotes(conn, [
        {"contract_id": "K1", "venue": "kalshi", "ts": store.to_ts(utc(2026, 7, 1, 12, 0)),
         "source": "hist", "mid": 0.40},
        {"contract_id": "K1", "venue": "kalshi", "ts": store.to_ts(utc(2026, 7, 1, 12, 5)),
         "source": "hist", "mid": None},          # a gap stays a gap
        {"contract_id": "K1", "venue": "kalshi", "ts": store.to_ts(utc(2026, 7, 1, 12, 10)),
         "source": "hist", "mid": 0.55},
    ])
    s = store.price_series(conn, "K1", field="mid")
    assert [p for _, p in s] == [0.40, 0.55]      # the NULL row is skipped, not zeroed
    t0 = int(utc(2026, 7, 1, 12, 0).timestamp())
    assert store.price_at(s, t0 + 60) == 0.40     # last at-or-before
    assert store.price_at(s, t0 - 60) is None     # never reaches forward


def test_polymarket_series_is_inverted_when_team_a_is_the_second_outcome(conn, utc):
    """The backfill stores P(outcomes[0]) only. Reading it as team_a's price
    when team_a is the OTHER outcome would score every such map backwards."""
    from core.db import store
    from core.reference.calib_data import pm_series_for_team
    store.upsert_contracts(conn, [{"contract_id": "0xabc", "venue": "polymarket",
                                   "family": "map_winner", "outcome_side": "Alpha"}])
    store.upsert_quotes(conn, [
        {"contract_id": "0xabc", "venue": "polymarket",
         "ts": store.to_ts(utc(2026, 7, 1, 12, 0)), "source": "hist", "last": 0.30}])
    prec = {"contract_id": "0xabc", "outcomes": ["Alpha", "Beta"]}
    assert pm_series_for_team(conn, prec, "Alpha")[0][1] == 0.30
    assert pm_series_for_team(conn, prec, "Beta")[0][1] == 0.70
    assert pm_series_for_team(conn, prec, "Gamma") == []      # unknown side -> no series
