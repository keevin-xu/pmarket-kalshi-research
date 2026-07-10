"""All tunables live here. A magic number anywhere else is a bug.

NOTHING in this file is frozen. Values are *proposed defaults*; a value
only becomes binding when it is copied into DECISIONS.md with a dated
FREEZE entry and its exact population. Analysis code reads CONFIG; it
must never hard-code a threshold.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB_PATH = DATA / "db" / "pmk.db"
RAW_DIR = DATA / "raw"
ARTIFACTS_DIR = DATA / "artifacts"

# Fixed-width UTC timestamp format: lexicographic order == chronological.
# Millisecond precision, explicit Z. All stored timestamps use this.
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # note: %f is microseconds; we trim to ms in helpers


@dataclass(frozen=True)
class Regimes:
    PRE_MATCH: str = "pre_match"
    IN_GAME: str = "in_game"


@dataclass(frozen=True)
class Venues:
    POLYMARKET: str = "polymarket"
    KALSHI: str = "kalshi"


@dataclass(frozen=True)
class ReferenceConfig:
    # Order-book venues: which price is the reference. NEVER de-vig an order book.
    orderbook_reference: str = "mid"          # "mid" | "last"
    # Pre-match calibration snapshot = price at kickoff.
    # In-game calibration checkpoint (a DEFINED game state, never last tick).
    in_game_checkpoint: str = "game_clock_10m"  # placeholder; freeze the exact definition
    # Price bucketing for calibration reliability curve.
    calibration_buckets: tuple[float, ...] = field(
        default_factory=lambda: tuple(round(x / 20, 3) for x in range(1, 20))  # 0.05..0.95
    )
    # Pass margin: how much better (>=) Kalshi's calibration error must be
    # vs Polymarket's to "pass". NOT FROZEN.
    calibration_pass_margin: float = 0.0


@dataclass(frozen=True)
class LeadLagConfig:
    # A divergence is a confirmed gap >= this (in price points), not a flicker.
    divergence_threshold: float = 0.02
    # Confirmation: gap must persist this many consecutive snapshots.
    confirmation_snapshots: int = 3
    # Window (seconds) over which convergence is measured after a divergence.
    convergence_window_s: int = 300
    # Bootstrap CI level.
    ci_level: float = 0.95


@dataclass(frozen=True)
class CensusConfig:
    # Min top-of-book depth per side at signal-moment for a market to count. NOT FROZEN.
    min_depth_usd_per_side: float = 250.0
    # Tier-1 coverage floor vs the neutral schedule. NOT FROZEN.
    tier1_coverage_floor: float = 0.80
    # Phase-1 market families (props always excluded).
    families_phase1: tuple[str, ...] = ("series_winner", "map_winner")
    # Fuzzy team-name join tolerance (minutes) for coverage checks.
    coverage_join_tolerance_min: int = 90


@dataclass(frozen=True)
class ParityConfig:
    # Fraction of matched contracts per family that must pass same-claim parity. NOT FROZEN.
    min_family_pass_rate: float = 0.95


@dataclass(frozen=True)
class BootstrapConfig:
    resamples: int = 10_000
    seed: int = 20260709  # seed everything; runs must be bit-identical


@dataclass(frozen=True)
class Config:
    regimes: Regimes = field(default_factory=Regimes)
    venues: Venues = field(default_factory=Venues)
    reference: ReferenceConfig = field(default_factory=ReferenceConfig)
    lead_lag: LeadLagConfig = field(default_factory=LeadLagConfig)
    census: CensusConfig = field(default_factory=CensusConfig)
    parity: ParityConfig = field(default_factory=ParityConfig)
    bootstrap: BootstrapConfig = field(default_factory=BootstrapConfig)

    # Endpoints from env (no secrets baked in).
    polymarket_gamma: str = os.environ.get("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com")
    polymarket_clob: str = os.environ.get("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com")
    polymarket_data: str = os.environ.get("POLYMARKET_DATA_BASE", "https://data-api.polymarket.com")
    kalshi_base: str = os.environ.get("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")

    db_path: str = str(DB_PATH)


CONFIG = Config()
