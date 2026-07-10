"""GATE G0 (part 2) — depth at the moments a signal would fire.

Books deepen near events; a random-instant median across mixed horizons
lies. Volume is NOT depth. Measure top-of-book depth per side at the
signal-moments for each regime, per venue, and compare to the frozen
CensusConfig.min_depth_usd_per_side. Historical bars give an UPPER BOUND on
depth (no fill guarantees) and must carry that flag.
"""
from __future__ import annotations


def depth_at_signal_moments(conn, venue: str, regime: str) -> dict:
    """Distribution of top-of-book depth per side at signal-moments for a
    (venue, regime). Returns median/quantiles + n, flagged hist vs live."""
    raise NotImplementedError("select quotes at signal-moment timestamps; summarize depth")
