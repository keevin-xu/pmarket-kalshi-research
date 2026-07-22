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

# A series is a time-ordered list of (unix_ts:int, price:float) observations.
Series = Sequence[tuple[int, float]]


@dataclass(frozen=True)
class Divergence:
    match_id: str
    regime: str
    t_start: int            # unix ts when the confirmed gap opened
    poly_price: float
    kalshi_price: float
    gap: float              # signed: kalshi - poly


def _asof(series: Series, t: int) -> float | None:
    """Most recent price at-or-before t (no lookahead, no fabrication)."""
    best = None
    for ts, px in series:
        if ts > t:
            break
        best = px
    return best


def _instants(poly: Series, kalshi: Series) -> list[int]:
    """Union of both series' timestamps, sorted (the compare grid)."""
    return sorted({t for t, _ in poly} | {t for t, _ in kalshi})


def detect_divergences(poly_series: Series, kalshi_series: Series, regime: str,
                       *, match_id: str = "") -> list[Divergence]:
    """Confirmed divergences per the frozen threshold + confirmation rule.
    As-of alignment; a run of >= confirmation_snapshots consecutive instants
    with |gap| >= threshold and CONSISTENT sign opens one divergence, then a
    one-window cooldown avoids re-counting the same event."""
    thr = CONFIG.lead_lag.divergence_threshold
    need = CONFIG.lead_lag.confirmation_snapshots
    window = CONFIG.lead_lag.convergence_window_s

    out: list[Divergence] = []
    run_len = 0
    run_sign = 0
    run_start: tuple[int, float, float] | None = None
    cooldown_until = -1
    for t in _instants(poly_series, kalshi_series):
        if t <= cooldown_until:
            continue
        p = _asof(poly_series, t)
        k = _asof(kalshi_series, t)
        if p is None or k is None:      # both venues must have been seen
            continue
        gap = k - p
        sign = 1 if gap > 0 else (-1 if gap < 0 else 0)
        if abs(gap) >= thr and sign != 0 and sign == (run_sign or sign):
            if run_len == 0:
                run_start = (t, p, k)
            run_sign = sign
            run_len += 1
            if run_len == need:
                t0, p0, k0 = run_start
                out.append(Divergence(match_id, regime, t0, p0, k0, k0 - p0))
                cooldown_until = t0 + window
                run_len = 0
                run_sign = 0
        else:
            run_len = 0
            run_sign = 0
    return out


def convergence_after(div: Divergence, poly_series: Series, kalshi_series: Series) -> float:
    """Signed lead L = sign(g0)*(dPoly + dKalshi) over convergence_window_s.
    L > 0 => Polymarket moved toward Kalshi => Kalshi led. None-safe: returns
    0.0 if the window-end price is missing (a gap contributes no lead)."""
    w = CONFIG.lead_lag.convergence_window_s
    pw = _asof(poly_series, div.t_start + w)
    kw = _asof(kalshi_series, div.t_start + w)
    if pw is None or kw is None:
        return 0.0
    g0 = div.gap
    s = 1.0 if g0 > 0 else -1.0
    d_poly = pw - div.poly_price
    d_kalshi = kw - div.kalshi_price
    return s * (d_poly + d_kalshi)


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
