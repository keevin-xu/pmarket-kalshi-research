"""G3 dataset builder: per covered map, build team_a's P(win) INTRADAY
series on both venues over the map's window, for the lead-lag state machine.

Kalshi series = candlestick mids (NO de-vig); Polymarket series = prices-
history last. Only maps where BOTH venues have a usable series are yielded
(the OE∩Polymarket-history overlap). Series are (unix_ts, price), time-
ordered; gaps stay gaps (no fabrication).
"""
from __future__ import annotations

from census import coverage as cov
from census import sweep
from config import CONFIG
from db import store
from ingest.kalshi import KalshiAdapter
from ingest.polymarket import PolymarketAdapter
from parity.settlement import _dedup, _find
from reference.calib_data import _side_key

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


def build_map_series(oe_paths: list[str], *, kalshi: KalshiAdapter | None = None,
                     poly: PolymarketAdapter | None = None) -> list[dict]:
    kalshi = kalshi or KalshiAdapter()
    poly = poly or PolymarketAdapter()

    oe = sweep.load_oe_map_results(oe_paths)
    krecs = _dedup(sweep.sweep_kalshi_map_results())
    precs = _dedup(sweep.sweep_polymarket_map_results())
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

        candles = kalshi.candlesticks(krec.get("series", "KXLOLMAP"), k_ticker,
                                      kickoff - _PREROLL_S, map_end + 300, 1)
        k_series = _kalshi_mid_series(candles)
        p_hist = poly.prices_history(toks[idx])
        p_series = [(int(pt["t"]), float(pt["p"])) for pt in p_hist
                    if kickoff - _PREROLL_S <= int(pt["t"]) <= map_end + 300]
        if len(k_series) < 2 or len(p_series) < 2:
            continue
        maps.append({"match_id": m["match_id"], "kickoff": kickoff,
                     "map_end": map_end, "kalshi": k_series, "poly": p_series})
    return maps


def slice_series(series: list[tuple[int, float]], lo: int, hi: int):
    return [(t, p) for t, p in series if lo <= t < hi]
