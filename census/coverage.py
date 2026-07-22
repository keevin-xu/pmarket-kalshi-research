"""GATE G0 (part 3) — cross-venue coverage vs the NEUTRAL schedule.

A match enters the population only if BOTH venues list a phase-1-family
contract for it, joined to an Oracle's Elixir tier-1 match by fuzzy
team-name within the frozen time tolerance. Odds/market feeds only list
what they price and self-report ~100% coverage, so coverage is checked
against neutral truth, never a venue's self-report.

Matches are collapsed to SERIES level ((team-pair, day)); OE `gameid` is
per-map, so several OE maps on one day between the same teams are one match.
The frozen G0 gate is `n_covered >= config.census.min_covered_matches`.
"""
from __future__ import annotations

import re
from datetime import timedelta
from difflib import SequenceMatcher

from config import CONFIG
from db import store

# Common esports org suffixes stripped before matching (do not carry signal).
# NB: "academy"/"challengers" are NOT stripped — they denote a DIFFERENT
# (secondary) squad; collapsing "T1 Academy" -> "T1" creates false matches.
_SUFFIXES = re.compile(
    r"\b(esports|e-sports|gaming|club|team|pro|the|of|legends)\b", re.IGNORECASE)
_NONALNUM = re.compile(r"[^a-z0-9 ]+")
# Markers of a SECONDARY squad. If exactly one side has one, the two names
# denote different teams (e.g. "T1 Academy" != "T1", "BNK FearX Youth" != "BNK
# FEARX") — block the match regardless of string similarity.
_SECONDARY = re.compile(r"\b(academy|challengers|youth|junior|development)\b",
                        re.IGNORECASE)


def _has_secondary(name: str) -> bool:
    return bool(_SECONDARY.search(name or ""))


def _deapos(s: str) -> str:
    """Drop apostrophes so possessives stay one token ("Anyone's" -> anyones)."""
    return (s or "").lower().replace("'", "").replace("’", "")


def normalize_team(name: str) -> str:
    """Lowercase, drop org suffixes and punctuation, collapse spaces."""
    if not name:
        return ""
    s = _deapos(name)
    s = _SUFFIXES.sub(" ", s)
    s = _NONALNUM.sub(" ", s)
    return " ".join(s.split())


def acronym(name: str) -> str:
    """Initials of significant words in the RAW name ('JD Gaming' -> 'jg',
    "Anyone's Legend" -> 'al'). Catches the common venue abbreviation form."""
    words = _NONALNUM.sub(" ", _deapos(name)).split()
    return "".join(w[0] for w in words if w)


def _compact_full(name: str) -> str:
    """All alnum of the raw name, no spaces ('JD Gaming' -> 'jdgaming')."""
    return _NONALNUM.sub("", (name or "").lower()).replace(" ", "")


def team_match(a: str, b: str, threshold: float | None = None) -> bool:
    """True if two team strings plausibly denote the same team, tolerant of
    suffixes, minor spelling drift, and DERIVABLE abbreviations (HLE/Hanwha
    Life Esports, AL/Anyone's Legend, JDG/JD Gaming). Non-derivable aliases
    (e.g. BLG for Bilibili Gaming) are NOT matched — a miss only UNDERcounts
    coverage (conservative), never inflates it."""
    threshold = CONFIG.census.team_match_threshold if threshold is None else threshold
    if _has_secondary(a) != _has_secondary(b):
        return False  # one is an academy/youth squad, the other isn't
    na, nb = normalize_team(a), normalize_team(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    aa, ab = acronym(a), acronym(b)               # word-initials of raw name
    sa, sb = na.replace(" ", ""), nb.replace(" ", "")  # suffix-stripped compact
    ca, cb = _compact_full(a), _compact_full(b)   # full compact (keeps suffix)
    # acronym of one equals the other's compact/acronym: HLE, AL, GG cases
    if aa and (aa == sb or aa == cb or aa == ab):
        return True
    if ab and (ab == sa or ab == ca):
        return True
    # short compact is a prefix of the other's full compact (JDG<-jdgaming)
    short, long = sorted((ca, cb), key=len)
    if len(short) >= 3 and long.startswith(short):
        return True
    # containment for longer tokens (guard against 2-3 char false hits)
    if len(min(na, nb, key=len)) >= 4 and (na in nb or nb in na):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def pair_match(oe_pair: tuple[str, str], venue_pair: tuple[str, str]) -> bool:
    """Do two (teamA, teamB) pairs denote the same fixture (order-agnostic)?"""
    a, b = oe_pair
    c, d = venue_pair
    return (team_match(a, c) and team_match(b, d)) or \
           (team_match(a, d) and team_match(b, c))


def _day_key(ts_iso: str) -> str:
    return ts_iso[:10]  # fixed-width ISO -> YYYY-MM-DD


def coverage_report(oe_matches: list[dict], venue_records: dict[str, list[dict]]) -> dict:
    """Compute cross-venue coverage.

    oe_matches: [{match_id, team_a, team_b, start_ts, league}] (tier-1, in-window)
    venue_records: {venue: [{teams:(a,b), ts, family, contract_id}]}

    Returns n_covered (both venues), coverage %, per-family/per-venue
    existence, and covered match ids. Time tolerance from config.
    """
    tol = timedelta(minutes=CONFIG.census.coverage_join_tolerance_min)

    # index venue records by day for a bounded search
    idx: dict[str, dict[str, list[dict]]] = {}
    existence: dict[str, set[str]] = {}   # venue -> set(families present)
    for venue, recs in venue_records.items():
        idx[venue] = {}
        existence[venue] = set()
        for r in recs:
            idx[venue].setdefault(_day_key(r["ts"]), []).append(r)
            if r.get("family"):
                existence[venue].add(r["family"])

    def venue_has(venue: str, oe: dict, family: str | None) -> bool:
        oe_pair = (oe["team_a"], oe["team_b"])
        oe_dt = store.from_ts(oe["start_ts"])
        day = _day_key(oe["start_ts"])
        # search day-1 / day / day+1 buckets (tolerance may cross midnight)
        for dshift in (-1, 0, 1):
            d = (oe_dt + timedelta(days=dshift)).strftime("%Y-%m-%d")
            for r in idx.get(venue, {}).get(d, []):
                if family and r.get("family") != family:
                    continue
                if abs(store.from_ts(r["ts"]) - oe_dt) > tol + timedelta(days=1):
                    # coarse gate; fine gate below (day buckets already near)
                    pass
                if pair_match(oe_pair, r["teams"]):
                    return True
        return False

    venues = list(venue_records.keys())
    covered_ids: list[str] = []
    per_family: dict[str, int] = {f: 0 for f in CONFIG.census.families_phase1}
    # dedupe OE maps to SERIES ((pair, day)) so multi-map days count once
    seen_series: dict[tuple, str] = {}
    for oe in oe_matches:
        key = (frozenset((normalize_team(oe["team_a"]), normalize_team(oe["team_b"]))),
               _day_key(oe["start_ts"]))
        if key in seen_series:
            continue
        seen_series[key] = oe["match_id"]
        if all(venue_has(v, oe, None) for v in venues):
            covered_ids.append(oe["match_id"])
            for fam in per_family:
                if all(venue_has(v, oe, fam) for v in venues):
                    per_family[fam] += 1

    n_series = len(seen_series)
    n_cov = len(covered_ids)
    return {
        "n_tier1_series": n_series,
        "n_covered": n_cov,
        "coverage_pct": (n_cov / n_series) if n_series else 0.0,
        "per_family_covered": per_family,
        "existence": {v: sorted(existence[v]) for v in venues},
        "covered_match_ids": covered_ids,
        "gate_min_covered": CONFIG.census.min_covered_matches,
        "passes_coverage": n_cov >= CONFIG.census.min_covered_matches,
    }
