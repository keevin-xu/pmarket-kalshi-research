"""GATE G0 (part 1) — population classification.

Classify each market from its question/event TEXT before computing any
statistic. Props dominate raw counts and poison every naive depth/spread
median, so they are excluded entirely. Tier-1 league matching uses
word-boundary code matching + substring name matching, with an EXCLUSION
list checked first ("LCK Challengers" must not count as "LCK"; two-letter
codes false-positive inside words).
"""
from __future__ import annotations

import re

# Families we care about; everything else (esp. props) is excluded.
# Patterns cover BOTH venues' real phrasings (pinned in DECISIONS.md recon):
# Kalshi map events say "Map N"; Polymarket says "Game N Winner"/"(BOn)"/
# "Match Result". map_winner is checked before series_winner so a per-map
# question never falls through to series.
FAMILY_PATTERNS = {
    "map_winner": [r"\bmap \d\b", r"\bgame \d\b"],
    "series_winner": [
        r"\bseries\b", r"\bto win the (match|series)\b", r"\bbest of\b",
        r"\bmatch (result|winner)\b", r"\bseries winner\b", r"\bBO\d\b",
    ],
}
PROP_MARKERS = [r"penta", r"first blood", r"first tower", r"total kills",
                r"\bMVP\b", r"total maps", r"champion", r"\bpick\b"]

# Tier-1 league CODES + full NAMES (venues sometimes spell out the tournament,
# e.g. "Mid-Season Invitational" instead of "MSI"). Exclusions checked FIRST.
TIER1_CODES = ["LCK", "LPL", "LEC", "LCS", "LTA", "LCP", "MSI", "Worlds"]
TIER1_NAMES = [
    "Mid-Season Invitational", "World Championship",
    "League of Legends Champions Korea", "LoL Pro League",
    "League of Legends EMEA Championship", "League of Legends Championship",
]
# Lookalikes that must NOT count as tier-1 (regional/academy/minor leagues).
EXCLUSIONS = [
    "LCK Challengers", "LPL Academy", "LEC Masters", "LCS Academy",
    "Prime League", "HLL", "Hellenic Legends", "Challengers", "Academy",
    "SuperLiga", "Ultraliga", "NLC", "PG Nationals", "1st Division",
]


def is_prop(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in PROP_MARKERS)


def classify_family(text: str) -> str | None:
    """Return family name or None. Props/unknown -> None (excluded)."""
    if is_prop(text):
        return None
    for fam, pats in FAMILY_PATTERNS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in pats):
            return fam
    return None


def is_tier1(text: str) -> bool:
    """True iff the text names a tier-1 league, exclusions removed first.

    NOTE: authoritative tier-1 for the coverage population comes from the
    Oracle's Elixir join (Kalshi map events carry team names but NOT the
    league). This text predicate is a pre-filter for venue-side text that
    DOES name the tournament (esp. Polymarket event titles).
    """
    cleaned = text
    for ex in EXCLUSIONS:
        cleaned = re.sub(re.escape(ex), " ", cleaned, flags=re.IGNORECASE)
    by_code = any(re.search(rf"\b{re.escape(code)}\b", cleaned) for code in TIER1_CODES)
    by_name = any(re.search(re.escape(name), cleaned, flags=re.IGNORECASE)
                  for name in TIER1_NAMES)
    return by_code or by_name
