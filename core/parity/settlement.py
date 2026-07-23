"""GATE G1 — cross-venue settlement parity.

No number crosses venues until the two contracts are proven the SAME CLAIM
per market family. This is a first-class gate, not a footnote: a comparison
across non-identical claims is a mapping bug that turns a settlement
difference into a fake edge.

For map_winner (the only surviving phase-1 family), "same claim" is proven
empirically and neutral-arbitrated per DECISIONS.md [2026-07-13] FREEZE:
align a Kalshi map market and a Polymarket "Game N Winner" market by fuzzy
team-pair + map number + day; on maps Oracle's Elixir records as PLAYED,
require kalshi_winner == polymarket_winner at >= min_family_pass_rate. OE is
the neutral arbiter (each venue's OE-agreement is reported). Unplayed maps
that a venue resolved to a team are void-handling breaks, enumerated not
averaged. Sets contracts.parity_ok downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from core.census import coverage as cov
from core.db import store
from core.sport import ParityParams


@dataclass(frozen=True)
class ParityResult:
    family: str
    n_aligned: int
    n_agree: int
    pass_rate: float
    passed_gate: bool
    verdict: str
    oe_agree_kalshi: float | None
    oe_agree_polymarket: float | None
    n_void_breaks: int
    disagreements: list[dict] = field(default_factory=list)


def contract_claims_match(poly_contract: dict, kalshi_contract: dict) -> bool:
    """Offline predicate: do these two map-winner contracts settle on the
    identical event? Same map number and same fuzzy team-pair; if BOTH carry
    a settled winner, the winners must agree. Pure function; unit-tested."""
    if poly_contract.get("map_no") != kalshi_contract.get("map_no"):
        return False
    if not cov.pair_match(tuple(poly_contract.get("teams", ("", ""))),
                          tuple(kalshi_contract.get("teams", ("", "")))):
        return False
    pw, kw = poly_contract.get("winner"), kalshi_contract.get("winner")
    if pw and kw and not cov.team_match(pw, kw):
        return False
    return True


def _dedup(records: list[dict]) -> list[dict]:
    """Collapse repeated (team-pair, day, map_no) records (a venue may list the
    same map in several events), preferring one that carries a settled winner."""
    best: dict[tuple, dict] = {}
    for r in records:
        key = (frozenset((cov.normalize_team(r["teams"][0]),
                          cov.normalize_team(r["teams"][1]))),
               r["ts"][:10], r["map_no"])
        if key not in best or (r.get("winner") and not best[key].get("winner")):
            best[key] = r
    return list(best.values())


def _index_by_day(records: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for r in records:
        idx.setdefault(r["ts"][:10], []).append(r)
    return idx


def _find(idx: dict[str, list[dict]], ref: dict, *, match_map: bool = True) -> dict | None:
    """First record in `idx` with the same fuzzy team-pair (and, unless
    match_map is False, the same map number) within ±1 day of `ref`."""
    pair = ref["teams"]
    dt = store.from_ts(ref["ts"])
    for dshift in (-1, 0, 1):
        day = (dt + timedelta(days=dshift)).strftime("%Y-%m-%d")
        for r in idx.get(day, []):
            if match_map and r["map_no"] != ref["map_no"]:
                continue
            if cov.pair_match(pair, r["teams"]):
                return r
    return None


def check_family_parity(oe_maps: list[dict], kalshi: list[dict], polymarket: list[dict],
                        parity_params=None, family: str = "map_winner") -> ParityResult:
    """Aggregate same-claim parity vs the sport's frozen parity params. Inputs
    are per-map records {teams, ts, map_no, winner} (OE = neutral arbiter).
    parity_params defaults to the generic ParityParams (== LoL's frozen values)."""
    parity_params = parity_params or ParityParams()
    kalshi, polymarket = _dedup(kalshi), _dedup(polymarket)
    kidx, pidx = _index_by_day(kalshi), _index_by_day(polymarket)
    oeidx = _index_by_day(oe_maps)  # OE holds only PLAYED maps

    n_aligned = n_agree = 0
    oe_k_agree = oe_p_agree = 0
    void_breaks = 0
    disagreements: list[dict] = []

    # (1) agreement on maps OE records as played
    for oe in oe_maps:
        oe_played = oe.get("winner")
        if not oe_played:
            continue
        k, p = _find(kidx, oe), _find(pidx, oe)
        if not (k and p):
            continue  # one-sided coverage, not a parity disagreement
        kw, pw = k.get("winner"), p.get("winner")
        if not (kw and pw):
            continue  # a venue hasn't settled this map -> not judgeable here
        n_aligned += 1
        agree = cov.team_match(kw, pw)
        n_agree += agree
        oe_k_agree += cov.team_match(kw, oe_played)
        oe_p_agree += cov.team_match(pw, oe_played)
        if not agree:
            disagreements.append({"kind": "winner_mismatch", "teams": oe["teams"],
                                  "map_no": oe["map_no"], "kalshi": kw,
                                  "polymarket": pw, "oe": oe_played})

    # (2) void handling: a venue resolved a map to a team that OE does NOT
    #     record as played, while OE DOES have the match (some map played).
    for venue, recs in (("kalshi", kalshi), ("polymarket", polymarket)):
        for r in recs:
            if not r.get("winner"):
                continue
            if _find(oeidx, r):                       # this exact map was played
                continue
            if _find(oeidx, r, match_map=False):      # match exists, this map did not
                void_breaks += 1
                disagreements.append({"kind": "void_break", "venue": venue,
                                      "teams": r["teams"], "map_no": r["map_no"]})

    rate = (n_agree / n_aligned) if n_aligned else 0.0
    min_n = parity_params.min_aligned_maps
    thr = parity_params.min_family_pass_rate
    if n_aligned < min_n:
        verdict = f"insufficient parity sample (n={n_aligned} < {min_n})"
        passed = False
    elif rate >= thr and void_breaks == 0:
        verdict = "PASS — same claim"
        passed = True
    else:
        verdict = "FAIL — claims differ / void mismatch"
        passed = False

    return ParityResult(
        family=family, n_aligned=n_aligned, n_agree=n_agree, pass_rate=rate,
        passed_gate=passed, verdict=verdict,
        oe_agree_kalshi=(oe_k_agree / n_aligned) if n_aligned else None,
        oe_agree_polymarket=(oe_p_agree / n_aligned) if n_aligned else None,
        n_void_breaks=void_breaks, disagreements=disagreements[:100],
    )
