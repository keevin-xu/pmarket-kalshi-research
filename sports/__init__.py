"""Sport registry. Adding a sport = implement the `Sport` interface in
sports/<sport>/ and register it here. The engine looks sports up by key
(`run --sport cs2 ...`); no core module names a sport."""
from __future__ import annotations

from sports.lol import LolSport

REGISTRY = {
    "lol": LolSport(),
    # "cs2": Cs2Sport(),   # add when scaffolded
}


def get_sport(key: str):
    if key not in REGISTRY:
        raise SystemExit(f"unknown sport {key!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[key]
