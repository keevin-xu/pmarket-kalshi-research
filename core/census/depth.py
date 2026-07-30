"""GATE G0 (part 2) — depth at the moments a signal would fire.

Books deepen near events; a random-instant median across mixed horizons
lies. Volume is NOT depth. Depth is measured on LIVE order-book snapshots
(`source='live'`): per the methodology, depth over settled/historical bars
is a bug — the book is gone at settlement, so a historical "depth" is
fiction. Each side's USD notional comes from the venue adapters
(price x size). We summarize min(bid,ask)-side depth vs the frozen
`CensusConfig.min_depth_usd_per_side`.
"""
from __future__ import annotations

import statistics


def _quantile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def depth_at_signal_moments(conn, venue: str, regime: str,
                            since: str | None = None) -> dict:
    """Distribution of top-of-book depth per side for a (venue, regime) over
    LIVE quotes. The per-market signal-moment depth = min(bid_side, ask_side)
    USD (both sides must be tradeable). Returns median/quantiles + n, flagged
    live; a market with a one-sided book is a below-depth discard, not a zero.

    `since` scopes the read to one observation window — normally the run that
    is reporting the number. Without it the median mixes every live row the
    database has ever held, so a change in WHICH markets are observed cannot
    be seen in the result, and an older run's population silently survives
    into a newer run's number.
    """
    sql = ("SELECT contract_id, bid_size_usd, ask_size_usd FROM quotes "
           "WHERE venue = ? AND source = 'live' AND regime = ? "
           "AND bid_size_usd IS NOT NULL AND ask_size_usd IS NOT NULL")
    params: list = [venue, regime]
    if since is not None:
        sql += " AND ts >= ?"
        params.append(since)
    rows = conn.execute(sql, params).fetchall()

    per_market = [min(r["bid_size_usd"], r["ask_size_usd"]) for r in rows]
    n = len(per_market)
    if n == 0:
        return {"venue": venue, "regime": regime, "n": 0, "source": "live",
                "since": since, "median_usd": None,
                "note": "no live two-sided book observed in this window"}
    return {
        "venue": venue,
        "regime": regime,
        "source": "live",
        "n": n,
        "since": since,
        "median_usd": statistics.median(per_market),
        "p25_usd": _quantile(per_market, 0.25),
        "p75_usd": _quantile(per_market, 0.75),
        "min_usd": min(per_market),
        "max_usd": max(per_market),
    }
