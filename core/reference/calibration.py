"""GATE G2 — calibration. Is a venue's price TRUE?

For each venue and regime, over resolved markets: snapshot the price at the
pre-registered point-in-time, pair with the NEUTRAL realized outcome (0/1),
bucket by price, and measure how closely realized frequencies match prices
(reliability curve + Brier + ECE + log-loss). CIs by event-block bootstrap.

Snapshot point-in-time (avoids the outcome-leak trap):
  * pre_match regime  -> price at kickoff (pre-match close)
  * in_game regime    -> a FIXED game-state checkpoint (config), NEVER the
                         last tick before resolution.

Pass rule (frozen in DECISIONS.md): Kalshi's calibration error <= Polymarket's
within config.reference.calibration_pass_margin. This module computes the
numbers; the PASS/FAIL verdict is assembled in analysis/report.py against the
frozen rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from core.analysis import metrics
from core.config import CONFIG
from core.sport import ReferenceParams


@dataclass(frozen=True)
class CalibrationPoint:
    """One resolved observation for calibration."""
    match_id: str
    venue: str
    regime: str
    price: float            # snapshot price at the fixed point-in-time
    outcome: int            # 0/1 from the NEUTRAL source


def calibration_report(points: Sequence[CalibrationPoint], venue: str, regime: str,
                       buckets=None) -> dict:
    """Reliability curve + Brier/ECE/log-loss + bootstrap CI on Brier, for
    one (venue, regime). `buckets` from the sport's reference params."""
    buckets = buckets or ReferenceParams().calibration_buckets
    sel = [p for p in points if p.venue == venue and p.regime == regime]
    if not sel:
        return {"venue": venue, "regime": regime, "n": 0}

    prices = [p.price for p in sel]
    outcomes = [p.outcome for p in sel]
    match_ids = [p.match_id for p in sel]
    curve = metrics.reliability_curve(prices, outcomes, buckets)

    def brier_of(vals: list[float]) -> float:
        # vals are per-observation squared errors; mean is the Brier score
        return sum(vals) / len(vals)

    sq_err = [(pr - y) ** 2 for pr, y in zip(prices, outcomes)]
    boot = metrics.event_block_bootstrap(match_ids, sq_err, brier_of)

    return {
        "venue": venue,
        "regime": regime,
        "n": len(sel),
        "brier": metrics.brier_score(prices, outcomes),
        "brier_ci": {"lo": boot["ci_lo"], "hi": boot["ci_hi"], "n_blocks": boot["n_blocks"]},
        "log_loss": metrics.log_loss(prices, outcomes),
        "ece": metrics.expected_calibration_error(curve, len(sel)),
        "reliability_curve": curve,
    }


def compare_venues(points: Sequence[CalibrationPoint], regime: str,
                   reference_params=None) -> dict:
    """Both venues' calibration in one regime, plus the frozen-margin
    comparison (does Kalshi's ECE beat Polymarket's within the margin?)."""
    reference_params = reference_params or ReferenceParams()
    buckets = reference_params.calibration_buckets
    poly = calibration_report(points, CONFIG.venues.POLYMARKET, regime, buckets)
    kalshi = calibration_report(points, CONFIG.venues.KALSHI, regime, buckets)
    margin = reference_params.calibration_pass_margin
    passed = None
    if poly.get("n") and kalshi.get("n"):
        passed = kalshi["ece"] <= poly["ece"] + margin
    return {"regime": regime, "polymarket": poly, "kalshi": kalshi,
            "kalshi_calibrated_vs_poly": passed, "margin": margin}
