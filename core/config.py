"""GLOBAL, cross-sport tunables only. Nothing sport-specific and nothing
frozen-per-sport lives here — that belongs in each sport's params.py, which
freezes on that sport's own first real run in its own DECISIONS.md.

A magic number in engine/analysis code is still a bug: global constants here,
frozen sport params in sports/<sport>/params.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # repo root
DATA = ROOT / "data"

# Fixed-width UTC timestamp format: lexicographic order == chronological.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # %f is microseconds; trimmed to ms in helpers


@dataclass(frozen=True)
class Regimes:
    PRE_MATCH: str = "pre_match"
    IN_GAME: str = "in_game"


@dataclass(frozen=True)
class Venues:
    POLYMARKET: str = "polymarket"
    KALSHI: str = "kalshi"


@dataclass(frozen=True)
class BootstrapConfig:
    resamples: int = 10_000
    seed: int = 20260709      # seed everything; runs must be bit-identical
    ci_level: float = 0.95    # methodological, shared across sports


@dataclass(frozen=True)
class RecorderConfig:
    # Generic recorder MECHANICS (not sport-specific): cadence, breaker, caps.
    poll_interval_s: int = 20
    tier1_only: bool = True
    catalog_ttl_s: int = 300
    cooldown_s: int = 3600
    max_markets_per_cycle: int = 400
    # Kalshi orderbook full-book parse UNVERIFIED until a live match pins it.
    kalshi_orderbook_verified: bool = False


@dataclass(frozen=True)
class CoreConfig:
    regimes: Regimes = field(default_factory=Regimes)
    venues: Venues = field(default_factory=Venues)
    bootstrap: BootstrapConfig = field(default_factory=BootstrapConfig)
    recorder: RecorderConfig = field(default_factory=RecorderConfig)

    # Venue endpoints from env (no secrets baked in). Venue MECHANICS, global.
    polymarket_gamma: str = os.environ.get("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com")
    polymarket_clob: str = os.environ.get("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com")
    polymarket_data: str = os.environ.get("POLYMARKET_DATA_BASE", "https://data-api.polymarket.com")
    kalshi_base: str = os.environ.get("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")


CONFIG = CoreConfig()
