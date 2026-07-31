"""Separating a real lead from a SAMPLING ARTIFACT.

G3 measures who converges toward whom. That measurement is only meaningful if
both venues are observed at comparable frequency, and in historical data they
are not: Kalshi serves 1-minute candles while Polymarket's `prices-history`
returns roughly 10-minute points. A coarsely-sampled series is stale between
observations, so when prices drift it will appear to "follow" a finely-sampled
one even if both venues held identical information at every instant.

The tier-a arm measured an in-game lead of L = 0.095 on exactly that
asymmetry, so the number cannot be read as information flow until the artifact
is bounded. This module bounds it three ways, always reported together:

  * **raw** — the series as observed. What G3 reports today.
  * **matched** — the finer series DOWNSAMPLED to the coarser one's cadence,
    so neither venue is advantaged. A lead that survives here is not explained
    by observation frequency.
  * **artifact_control** — the fine series against ITSELF downsampled. Both
    arms carry identical information by construction, so any lead measured
    here is pure cadence artifact. This is the number that says how much of
    the raw lead was never real.

Downsampling KEEPS ONLY OBSERVED POINTS (the last in each bucket). Nothing is
interpolated or forward-filled into the series; staleness is represented the
way the data actually is — by the absence of a point — and read as-of, which
is what `lead_lag._asof` already does.
"""
from __future__ import annotations

import statistics
from typing import Sequence

from core.config import CONFIG
from core.reference import lead_lag

Series = Sequence[tuple[int, float]]


def cadence_s(series: Series) -> float | None:
    """Median seconds between consecutive observations, or None if too short.

    Median rather than mean: a single long gap (a market pausing, a feed
    outage) must not be read as the series' normal frequency.
    """
    if series is None or len(series) < 2:
        return None
    gaps = [b - a for (a, _), (b, _) in zip(series, series[1:]) if b > a]
    return float(statistics.median(gaps)) if gaps else None


def downsample(series: Series, grid_s: float) -> list[tuple[int, float]]:
    """Thin a series to at most one observation per `grid_s` bucket.

    Keeps the LAST real observation in each bucket — never a synthesized or
    averaged value — so the result is a strict subset of what was observed.
    That is what makes it a fair handicap: the coarse arm knows less because
    it looked less often, not because its data was altered.
    """
    if not series or not grid_s or grid_s <= 0:
        return list(series or [])
    out: list[tuple[int, float]] = []
    bucket = None
    for t, p in series:
        b = int(t // grid_s)
        if bucket is None or b != bucket:
            out.append((t, p))
            bucket = b
        else:
            out[-1] = (t, p)          # later observation in the same bucket wins
    return out


def _run(poly: Series, kalshi: Series, regime: str, match_id: str, ll_params):
    """One lead-lag pass; returns (divergences, convergences)."""
    divs = lead_lag.detect_divergences(poly, kalshi, regime, match_id=match_id,
                                       ll_params=ll_params)
    convs = [lead_lag.convergence_after(d, poly, kalshi, ll_params=ll_params)
             for d in divs]
    return divs, convs


def compare_cadence(maps: list[dict], regime: str, *, ll_params=None,
                    slicer=None) -> dict:
    """Run the three arms across maps and report them side by side.

    `maps` are the G3 dataset rows ({match_id, kickoff, map_end, kalshi, poly}).
    `slicer` optionally restricts each series to the regime's window; pass
    `leadlag_data.slice_series`-style callable taking (series, lo, hi).
    """
    arms = {"raw": ([], [], []), "matched": ([], [], []),
            "artifact_control": ([], [], [])}
    cadences = {"kalshi": [], "polymarket": []}

    for mp in maps:
        k_series, p_series = list(mp["kalshi"]), list(mp["poly"])
        if slicer is not None:
            lo, hi = mp.get("_lo"), mp.get("_hi")
            if lo is not None and hi is not None:
                k_series, p_series = slicer(k_series, lo, hi), slicer(p_series, lo, hi)
        if len(k_series) < 2 or len(p_series) < 2:
            continue
        k_cad, p_cad = cadence_s(k_series), cadence_s(p_series)
        if k_cad:
            cadences["kalshi"].append(k_cad)
        if p_cad:
            cadences["polymarket"].append(p_cad)

        # coarser cadence wins; the finer series is handicapped down to it
        grid = max(filter(None, (k_cad, p_cad)), default=None)
        k_matched = downsample(k_series, grid) if grid else k_series
        p_matched = downsample(p_series, grid) if grid else p_series

        for name, (poly_s, kalshi_s) in (
            ("raw", (p_series, k_series)),
            ("matched", (p_matched, k_matched)),
            # identical information on both sides; only the cadence differs
            ("artifact_control", (downsample(k_series, grid) if grid else k_series,
                                  k_series)),
        ):
            divs, convs = _run(poly_s, kalshi_s, regime, mp["match_id"], ll_params)
            arms[name][0].extend(divs)
            arms[name][1].extend(convs)
            arms[name][2].extend([mp["match_id"]] * len(divs))

    report = {}
    for name, (divs, convs, ids) in arms.items():
        report[name] = lead_lag.lead_lag_report(divs, convs, ids, regime)

    med = lambda xs: round(statistics.median(xs), 1) if xs else None  # noqa: E731
    report["cadence_s"] = {"kalshi_median": med(cadences["kalshi"]),
                           "polymarket_median": med(cadences["polymarket"])}
    report["reading"] = _reading(report)
    return report


def _reading(report: dict) -> str:
    """State plainly what the three arms together do and do not support."""
    raw = report["raw"]["signed_convergence"].get("point")
    matched = report["matched"]["signed_convergence"].get("point")
    control = report["artifact_control"]["signed_convergence"].get("point")
    if raw is None or control is None:
        return "insufficient sample to separate lead from cadence artifact"
    if abs(control) >= abs(raw) * 0.5:
        return ("cadence artifact accounts for a large share of the raw lead — "
                "the raw number is not evidence of information flow")
    if matched is not None and report["matched"]["leader"]:
        return ("lead survives matched cadence — not explained by observation "
                "frequency alone")
    return ("lead does NOT survive matched cadence — consistent with a sampling "
            "artifact")


__all__ = ["cadence_s", "downsample", "compare_cadence", "CONFIG"]
