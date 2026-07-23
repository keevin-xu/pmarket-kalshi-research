"""The seam between the sport-agnostic engine and each sport.

A `Sport` bundles everything that varies per sport behind one small interface:
population classification, venue discovery, neutral outcomes, the in-game
checkpoint, same-claim parity, and — crucially — its OWN frozen params
(`SportParams`). No gate in core/ may name a sport; it calls methods on the
`sport` it is handed and reads `sport.params`.

`SportParams` holds the frozen-shape dataclasses. Their VALUES are set per
sport in sports/<sport>/params.py and frozen in that sport's DECISIONS.md.
LoL's numbers (e.g. in_game_checkpoint_s=600, tier1_leagues, families) are
LoL-specific and must not bind any other sport.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# --- frozen-shape param dataclasses (values set per sport) --------------------
@dataclass(frozen=True)
class ReferenceParams:
    orderbook_reference: str = "mid"            # "mid" | "last"; NEVER de-vig a book
    in_game_checkpoint_s: int = 600             # seconds from kickoff (a DEFINED state)
    calibration_buckets: tuple[float, ...] = field(
        default_factory=lambda: tuple(round(x / 20, 3) for x in range(1, 20)))
    calibration_pass_margin: float = 0.0


@dataclass(frozen=True)
class LeadLagParams:
    divergence_threshold: float = 0.02
    confirmation_snapshots: int = 3
    convergence_window_s: int = 300
    min_divergences: int = 30


@dataclass(frozen=True)
class CensusParams:
    min_depth_usd_per_side: float = 250.0
    min_covered_matches: int = 60
    tier1_coverage_floor: float = 0.80
    families_phase1: tuple[str, ...] = ()
    coverage_join_tolerance_min: int = 90
    team_match_threshold: float = 0.85
    tier1_leagues: tuple[str, ...] = ()          # neutral-source league codes
    window_start: str = ""                       # ISO fixed-width UTC


@dataclass(frozen=True)
class ParityParams:
    min_family_pass_rate: float = 0.95
    min_aligned_maps: int = 30


@dataclass(frozen=True)
class SportParams:
    key: str
    census: CensusParams
    parity: ParityParams
    reference: ReferenceParams
    lead_lag: LeadLagParams
    bounded_verdict_date: str                    # G4 re-judgment date (frozen)
    # per-sport isolated storage (own DB + own artifacts + own raw + own ledger)
    db_path: str
    raw_dir: str
    artifacts_dir: str
    decisions_path: str


@runtime_checkable
class Sport(Protocol):
    """Everything sport-specific behind one interface. Implemented in
    sports/<sport>/__init__.py."""
    key: str
    params: SportParams

    # population classification (from question/event text) -----------------
    def classify_family(self, text: str) -> str | None: ...
    def is_prop(self, text: str) -> bool: ...
    def is_tier1(self, text: str, league: str | None = None) -> bool: ...

    # venue discovery -------------------------------------------------------
    def polymarket_tag(self) -> str: ...                 # e.g. "league-of-legends"
    def kalshi_series(self) -> dict[str, str]: ...        # {series_ticker: family}

    # neutral ground truth (schedule + outcomes) ---------------------------
    def load_map_results(self, paths: list[str]) -> list[dict]: ...   # per-map records
    def outcome_paths(self) -> list[str]: ...             # neutral-source files

    # same-claim parity -----------------------------------------------------
    def families(self) -> tuple[str, ...]: ...
