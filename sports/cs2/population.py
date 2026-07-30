"""CS2 population classification — from market TEXT, before any statistic.

Two things differ from LoL and both come from recon (see DECISIONS.md
[2026-07-30] NOTEs):

  * Polymarket phrases the series moneyline as the EVENT TITLE itself
    ("Counter-Strike: A vs B (BO3) - <Tournament>"), never "Match Winner",
    so match_winner is detected by the `(BO<n>)` marker, not by the word
    "match". Map markets are "… - Map N Winner".
  * Tier-1 is NOT a name list. The neutral source publishes `tier`
    ("s"/"a"/"b"/"c"/"d") on every match, so `is_tier1` takes that field and
    the text predicate survives only as a cheap pre-filter for venue titles.

Prop markers are deliberately conservative: the text handed to these
functions is `event_title + question`, so a marker that can collide with a
team or tournament name (a bare "MVP" — MVP is a real org) would silently
drop real fixtures. Anything not positively classified returns None and is
excluded anyway.
"""
from __future__ import annotations

import re

# map_winner needs BOTH a map number AND a winner notion, else "Map 1 Total
# Rounds" and "Map Handicap" leak in as maps.
_MAP_NO = re.compile(r"\bmap (\d+)\b", re.IGNORECASE)
_WINNER = re.compile(r"\bwinner\b|\bto win\b|\bwins\b", re.IGNORECASE)
# Polymarket's series moneyline marker; Kalshi's family comes from the series
# ticker (KXCS2GAME), not from text.
MATCH_PATTERNS = [r"\(BO\s?\d\)", r"\bmatch (result|winner)\b",
                  r"\bto win the (match|series)\b", r"\bseries winner\b"]

PROP_MARKERS = [
    r"total rounds", r"total maps?", r"games total", r"\bhandicap\b",
    r"over/under", r"\bO/U\b", r"pistol round", r"first blood", r"first kill",
    r"\bto reach\b", r"(exact|correct) score", r"player of the (year|tournament)",
    r"team of the year", r"\bace\b(?= round)", r"\bclutch\b",
]

# Text pre-filter only — a cheap narrowing of venue titles before the neutral
# join. The AUTHORITY is the neutral tier field (see is_tier1). Exclusions are
# checked first: bo3.gg itself tiers "BLAST Bounty … Closed Qualifier" as a and
# "StarLadder … North American Qualifier" as c, which is what these encode.
TIER1_NAMES = [
    "IEM", "Intel Extreme Masters", "BLAST Premier", "BLAST Open",
    "BLAST Rivals", "ESL Pro League", "ESL One", "PGL", "StarLadder",
    "FISSURE", "Major", "Esports World Cup", "CS Asia Championships",
]
EXCLUSIONS = [
    "Qualifier", "Open Qual", "Closed Qual", "Contenders", "Academy",
    "Challenger League", "CCT", "Conquest", "Stake Ranked", "ESEA",
    "Cash Cup", "Relegation",
]

TIER1_CODE = "s"          # the neutral source's tier value that means tier-1


def is_prop(text: str) -> bool:
    t = text or ""
    return any(re.search(p, t, re.IGNORECASE) for p in PROP_MARKERS)


def map_number(text: str) -> int | None:
    """Map number from CS2 question text ("… - Map 2 Winner" -> 2).

    CS2 says "Map N" where LoL says "Game N"; the venue-side sweep needs this
    to pair a map contract with the neutral map record.
    """
    m = _MAP_NO.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def classify_family(text: str) -> str | None:
    """Family name, or None for props/unknown (excluded).

    map_winner is checked FIRST: a map market's text also carries the event
    title's "(BO3)", so the order decides whether "Map 1 Winner" is read as a
    map or as the series.
    """
    if is_prop(text):
        return None
    if _MAP_NO.search(text or "") and _WINNER.search(text or ""):
        return "map_winner"
    if any(re.search(p, text or "", re.IGNORECASE) for p in MATCH_PATTERNS):
        return "match_winner"
    return None


def is_tier1(text: str, tier: str | None = None) -> bool:
    """True iff this is a tier-1 (S-tier) fixture.

    `tier` is the NEUTRAL source's own field (bo3.gg `matches.tier`) and is
    authoritative whenever present — recon measured it agreeing with
    `tournaments.tier` on 1790/1790 rows in the window. The text branch is
    only for venue-side titles, which carry a tournament name but no tier.
    """
    if tier is not None:
        return str(tier).strip().lower() == TIER1_CODE
    cleaned = text or ""
    for ex in EXCLUSIONS:
        cleaned = re.sub(re.escape(ex), " ", cleaned, flags=re.IGNORECASE)
    return any(re.search(re.escape(name), cleaned, flags=re.IGNORECASE)
               for name in TIER1_NAMES)
