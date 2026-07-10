"""GATE G3 — lead-lag. Does a venue get there FIRST?

Using aligned price time-series for both venues, detect confirmed
divergences (gap >= config.lead_lag.divergence_threshold, persisting
config.lead_lag.confirmation_snapshots; not a one-tick flicker). For each
divergence, over the following convergence_window_s, measure which venue the
OTHER converges toward and by how much. Signed aggregate: positive = Kalshi
leads (Polymarket moves to Kalshi). Corroborate with a symmetric lagged
cross-correlation. Per regime (leadership can flip). Event-block bootstrap CI.

Regime caveat (mirrors the map-end repricer failure): in-game both venues
read the same visible game state; if both are competent they may reprice
together within seconds -> no tradeable lag however large the instantaneous
gap looks. If the reprice completes before a trigger could fire, there is no
lag edge and the report says so.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from config import CONFIG


@dataclass(frozen=True)
class Divergence:
    match_id: str
    regime: str
    t_start: str            # fixed-width ts when the confirmed gap opened
    poly_price: float
    kalshi_price: float
    gap: float              # signed: kalshi - poly


def detect_divergences(poly_series, kalshi_series, regime: str) -> list[Divergence]:
    """Align the two time-series and return confirmed divergences per the
    frozen threshold + confirmation rule. Alignment is as-of (no lookahead)."""
    raise NotImplementedError("align series; apply threshold + confirmation state machine")


def convergence_after(div: Divergence, poly_series, kalshi_series) -> float:
    """Signed convergence over convergence_window_s: how far the FOLLOWER
    moved toward the leader's price. Positive => Kalshi led."""
    raise NotImplementedError("measure post-divergence convergence direction")


def lead_lag_report(divergences: Sequence[Divergence], convergences: Sequence[float],
                    match_ids: Sequence[str], regime: str) -> dict:
    """Aggregate signed convergence with an event-block bootstrap CI. Pass =
    CI excludes zero (direction is the leader). Assembled vs frozen rule in
    analysis/report.py."""
    from analysis import metrics
    boot = metrics.event_block_bootstrap(list(match_ids), list(convergences))
    lead = None
    if boot["ci_lo"] is not None:
        if boot["ci_lo"] > 0:
            lead = CONFIG.venues.KALSHI
        elif boot["ci_hi"] < 0:
            lead = CONFIG.venues.POLYMARKET
    return {"regime": regime, "n_divergences": len(divergences),
            "signed_convergence": boot, "leader": lead}
