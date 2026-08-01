"""G3 dataset builder: per covered map, build team_a's P(win) INTRADAY
series on both venues over the map's window, for the lead-lag state machine.

Kalshi series = candlestick mids (NO de-vig); Polymarket series = prices-
history last. Only maps where BOTH venues have a usable series are yielded
(the OE∩Polymarket-history overlap). Series are (unix_ts, price), time-
ordered; gaps stay gaps (no fabrication).
"""
from __future__ import annotations

from core.census import coverage as cov
from core.census import sweep
from core.config import CONFIG
from core.db import store
from core.ingest.base import Pacer, with_retries
from core.ingest.kalshi import KalshiAdapter
from core.ingest.polymarket import PolymarketAdapter
# Bulk per-map vendor reads: paced like the backfill so a gate cannot die
# half-swept on a rate limit (a vendor refusal is an outage, not a finding).
_K_PACE = Pacer(CONFIG.backfill.kalshi_min_interval_s)
_P_PACE = Pacer(CONFIG.backfill.polymarket_min_interval_s)

from core.parity.settlement import _dedup, _find
from core.reference import calib_data
from core.reference.calib_data import _side_key

_PREROLL_S = 6 * 3600      # look back 6h before kickoff for pre-match divergences
_DEFAULT_MAP_S = 2400      # fallback map length if OE gamelength missing


def _kalshi_mid_series(candles: list[dict]) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for c in candles:
        try:
            bid = float(c["yes_bid"]["close_dollars"])
            ask = float(c["yes_ask"]["close_dollars"])
            ts = int(c["end_period_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid <= 0 and ask <= 0:
            continue
        out.append((ts, round((bid + ask) / 2.0, 6)))
    return out


def build_map_series(sport, oe_paths: list[str], *, conn=None,
                     family: str = "map_winner", source: str = "hist",
                     kalshi: KalshiAdapter | None = None,
                     poly: PolymarketAdapter | None = None) -> list[dict]:
    """Paired intraday series per covered map, read from the LOCAL STORE.
    Vendor history rolls off; the store is the system of record."""
    if conn is None:
        raise ValueError("build_map_series reads the local store; pass a connection")

    oe = sweep.neutral_results(sport, oe_paths, family)
    k_raw, p_raw = sweep.venue_results(sport, family)
    krecs, precs = _dedup(k_raw), _dedup(p_raw)
    kidx: dict[str, list[dict]] = {}
    pidx: dict[str, list[dict]] = {}
    for r in krecs:
        kidx.setdefault(r["ts"][:10], []).append(r)
    for r in precs:
        pidx.setdefault(r["ts"][:10], []).append(r)

    maps: list[dict] = []
    for m in oe:
        krec, prec = _find(kidx, m), _find(pidx, m)
        if not (krec and prec):
            continue
        team_a = m["teams"][0]
        kickoff = int(store.from_ts(m["ts"]).timestamp())
        gamelen = m.get("gamelen_s") or _DEFAULT_MAP_S
        map_end = kickoff + gamelen

        k_ticker = next((tk for team, tk in (krec.get("team_markets") or {}).items()
                         if team and cov.team_match(team_a, team)), None)
        idx = _side_key(prec.get("outcomes") or [], team_a)
        toks = prec.get("tokens") or []
        if not k_ticker or idx is None or idx >= len(toks):
            continue

        lo, hi = kickoff - _PREROLL_S, map_end + 300
        k_series = store.price_series(
            conn, k_ticker, field=calib_data.price_field(CONFIG.venues.KALSHI, source),
            source=source, lo=lo, hi=hi)
        p_series = [(t, p) for t, p in
                    calib_data.pm_series_for_team(conn, prec, team_a, source)
                    if lo <= t <= hi]
        if len(k_series) < 2 or len(p_series) < 2:
            continue
        maps.append({"match_id": m["match_id"], "kickoff": kickoff,
                     "map_end": map_end, "kalshi": k_series, "poly": p_series})
    return maps


def slice_series(series: list[tuple[int, float]], lo: int, hi: int):
    return [(t, p) for t, p in series if lo <= t < hi]
