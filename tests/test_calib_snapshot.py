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
