from reference.calibration import CalibrationPoint, calibration_report, compare_venues
from config import CONFIG


def _pts(venue, regime, data):
    return [CalibrationPoint(m, venue, regime, p, y) for m, p, y in data]


def test_calibration_report_shapes():
    pts = _pts("kalshi", "pre_match",
               [("m1", 0.6, 1), ("m2", 0.6, 0), ("m3", 0.7, 1), ("m4", 0.7, 1)])
    rep = calibration_report(pts, "kalshi", "pre_match")
    assert rep["n"] == 4
    assert "brier" in rep and "ece" in rep and rep["reliability_curve"]


def test_compare_venues_returns_both():
    pts = (_pts(CONFIG.venues.KALSHI, "pre_match", [("m1", 0.6, 1), ("m2", 0.6, 0)]) +
           _pts(CONFIG.venues.POLYMARKET, "pre_match", [("m1", 0.55, 1), ("m2", 0.55, 0)]))
    cmp = compare_venues(pts, "pre_match")
    assert cmp["polymarket"]["n"] == 2 and cmp["kalshi"]["n"] == 2
