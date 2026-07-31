"""G2 dataset builder: snapshot each covered map's price on BOTH venues at
the frozen point-in-time, paired with the neutral outcome.

Prices come from the LOCAL STORE (`source='hist'`), not from the vendors.
Both venues' history rolls off — Kalshi ~68 days, Polymarket ~30 — so a gate
that re-fetches sees less than the run before it and its numbers stop being
reproducible in the one direction nobody checks. A map with no stored series
is a counted skip, never a fabricated point.

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
from core.ingest.base import Pacer, with_retries
from core.ingest.kalshi import KalshiAdapter
from core.ingest.polymarket import PolymarketAdapter
# Bulk per-map vendor reads: paced like the backfill so a gate cannot die
# half-swept on a rate limit (a vendor refusal is an outage, not a finding).
_K_PACE = Pacer(CONFIG.backfill.kalshi_min_interval_s)
_P_PACE = Pacer(CONFIG.backfill.polymarket_min_interval_s)

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


def pm_series_for_team(conn, prec: dict, team_a: str) -> list[tuple[int, float]]:
    """Polymarket series as P(team_a wins), from the store.

    The backfill stores ONE series per market — the probability of
    `outcomes[0]`, since a binary CLOB's other leg is its complement. So when
    team_a is the second outcome the series must be INVERTED; using it as-is
    would silently score every such map against the wrong side.
    """
    outs = prec.get("outcomes") or []
    raw = store.price_series(conn, prec.get("contract_id"), field="last")
    if not raw or not outs:
        return []
    if cov.team_match(team_a, outs[0]):
        return raw
    if len(outs) > 1 and cov.team_match(team_a, outs[1]):
        return [(t, round(1.0 - p, 6)) for t, p in raw]
    return []


def build_points(sport, oe_paths: list[str], *, conn=None, family: str = "map_winner",
                 kalshi: KalshiAdapter | None = None,
                 poly: PolymarketAdapter | None = None) -> list[CalibrationPoint]:
    if conn is None:
        raise ValueError("build_points reads the local store; pass a connection")
    checkpoint_s = sport.params.reference.in_game_checkpoint_s

    oe = sweep.neutral_results(sport, oe_paths, family)
    k_raw, p_raw = sweep.venue_results(sport, family)
    krecs, precs = _dedup(k_raw), _dedup(p_raw)
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

        # --- Kalshi: team_a's market -> stored MID series (no de-vig) ---------
        k_ticker = next((tk for team, tk in (krec.get("team_markets") or {}).items()
                         if team and cov.team_match(team_a, team)), None)
        if k_ticker:
            k_series = store.price_series(conn, k_ticker, field="mid")
            for regime, tgt in targets.items():
                mid = store.price_at(k_series, tgt)
                if mid is not None:
                    points.append(CalibrationPoint(m["match_id"], CONFIG.venues.KALSHI,
                                                   regime, mid, outcome))

        # --- Polymarket: stored LAST series, oriented to team_a ---------------
        p_series = pm_series_for_team(conn, prec, team_a)
        for regime, tgt in targets.items():
            pr = store.price_at(p_series, tgt)
            if pr is not None:
                points.append(CalibrationPoint(m["match_id"], CONFIG.venues.POLYMARKET,
                                               regime, pr, outcome))
    return points
