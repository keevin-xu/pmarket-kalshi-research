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
FAMILY_PATTERNS = {
    "series_winner": [r"\bseries\b", r"\bto win the (match|series)\b", r"\bbest of\b"],
    "map_winner": [r"\bmap \d\b", r"\bgame \d\b"],
}
PROP_MARKERS = [r"penta", r"first blood", r"first tower", r"total kills", r"\bMVP\b"]

# Tier-1 league codes; exclusion list checked FIRST.
TIER1_CODES = ["LCK", "LPL", "LEC", "LCS", "LCP", "MSI", "Worlds"]
EXCLUSIONS = ["LCK Challengers", "LPL Academy", "LEC Masters", "LCS Academy"]


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
    """True iff the text names a tier-1 league, exclusions removed first."""
    cleaned = text
    for ex in EXCLUSIONS:
        cleaned = re.sub(re.escape(ex), " ", cleaned, flags=re.IGNORECASE)
    return any(re.search(rf"\b{re.escape(code)}\b", cleaned) for code in TIER1_CODES)
