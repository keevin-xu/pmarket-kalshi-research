"""G2 dataset builder: snapshot each covered map's price on BOTH venues at
the frozen point-in-time, paired with the neutral Oracle's Elixir outcome.

One point per map, per venue, per regime, using the Blue-side team (team_a):
price = P(team_a wins the map), outcome = 1 iff OE says team_a won. Snapshot
is the last quote at-or-before the target instant (no lookahead):
  * pre_match: kickoff (OE map date)
  * in_game:   kickoff + 600 s (10-min game clock), if gamelen >= 600 s
Prices are order-book MID (Kalshi candles) / last (Polymarket history), NO
de-vig. Network fetches go through the adapters; a gap stays a gap (None).
"""
from __future__ import annotations

from datetime import timedelta

from core.census import coverage as cov
from core.census import sweep
from core.config import CONFIG
from core.db import store
from core.ingest.kalshi import KalshiAdapter
from core.ingest.polymarket import PolymarketAdapter
from core.parity.settlement import _dedup, _find
from core.reference.calibration import CalibrationPoint

def _pm_price_at(history: list[dict], target_ts: int) -> float | None:
    """Last Polymarket price at-or-before target (no lookahead)."""
    best = None
    for pt in history:
        if pt.get("t", 0) > target_ts:
            break
        best = pt
    return None if best is None else float(best["p"])


def _side_key(names, team_a: str):
    """Index/key in `names` whose team fuzzy-matches team_a, else None."""
    for i, nm in enumerate(names):
        if cov.team_match(team_a, nm):
            return i
    return None


def build_points(sport, oe_paths: list[str], *, kalshi: KalshiAdapter | None = None,
                 poly: PolymarketAdapter | None = None) -> list[CalibrationPoint]:
    kalshi = kalshi or KalshiAdapter()
    poly = poly or PolymarketAdapter()
    checkpoint_s = sport.params.reference.in_game_checkpoint_s

    oe = sport.load_map_results(oe_paths)
    krecs = _dedup(sweep.sweep_kalshi_map_results(sport))
    precs = _dedup(sweep.sweep_polymarket_map_results(sport))
    kidx = {}
    pidx = {}
    for r in krecs:
        kidx.setdefault(r["ts"][:10], []).append(r)
    for r in precs:
        pidx.setdefault(r["ts"][:10], []).append(r)

    points: list[CalibrationPoint] = []
    for m in oe:
        krec, prec = _find(kidx, m), _find(pidx, m)
        if not (krec and prec):
            continue
        team_a = m["teams"][0]
        outcome = 1 if cov.team_match(team_a, m["winner"]) else 0
        kickoff = int(store.from_ts(m["ts"]).timestamp())
        gamelen = m.get("gamelen_s")
        targets = {CONFIG.regimes.PRE_MATCH: kickoff}
        if gamelen and gamelen >= checkpoint_s:
            targets[CONFIG.regimes.IN_GAME] = kickoff + checkpoint_s

        # --- Kalshi: team_a's market -> candlesticks -> mid at target(s) ------
        k_ticker = next((tk for team, tk in (krec.get("team_markets") or {}).items()
                         if team and cov.team_match(team_a, team)), None)
        if k_ticker:
            candles = kalshi.candlesticks(krec.get("series"), k_ticker,
                                          kickoff - 86400, kickoff + 7200, 1)
            for regime, tgt in targets.items():
                mid = KalshiAdapter.candle_mid_at(candles, tgt)
                if mid is not None:
                    points.append(CalibrationPoint(m["match_id"], CONFIG.venues.KALSHI,
                                                   regime, mid, outcome))

        # --- Polymarket: team_a's token -> prices-history -> price at target(s)
        idx = _side_key(prec.get("outcomes") or [], team_a)
        toks = prec.get("tokens") or []
        if idx is not None and idx < len(toks):
            hist = poly.prices_history(toks[idx])
            for regime, tgt in targets.items():
                pr = _pm_price_at(hist, tgt)
                if pr is not None:
                    points.append(CalibrationPoint(m["match_id"], CONFIG.venues.POLYMARKET,
                                                   regime, pr, outcome))
    return points
