"""FROZEN LoL params. These are LoL-specific and bind ONLY LoL — every value
here traces to a dated FREEZE/RULING in sports/lol/DECISIONS.md. Carried over
verbatim from the pre-refactor config.py; no threshold moved.
"""
from __future__ import annotations

from core.config import ROOT
from core.sport import (CensusParams, LeadLagParams, ParityParams,
                        ReferenceParams, SportParams)

_DATA = ROOT / "data" / "lol"

LOL_PARAMS = SportParams(
    key="lol",
    census=CensusParams(
        min_depth_usd_per_side=250.0,          # FROZEN 2026-07-12 (G0)
        min_covered_matches=60,                # RULING 2026-07-12 (count floor)
        tier1_coverage_floor=0.80,             # diagnostic only
        families_phase1=("series_winner", "map_winner"),
        coverage_join_tolerance_min=90,
        team_match_threshold=0.85,
        tier1_leagues=("LCK", "LPL", "LEC", "LCS", "LTA", "LCP", "MSI", "WLDs"),
        window_start="2025-01-01T00:00:00.000Z",
    ),
    parity=ParityParams(
        min_family_pass_rate=0.95,             # FROZEN 2026-07-13 (G1)
        min_aligned_maps=30,
    ),
    reference=ReferenceParams(
        orderbook_reference="mid",
        in_game_checkpoint_s=600,              # FROZEN 2026-07-13 (G2): 10-min game clock
        calibration_pass_margin=0.0,
    ),
    lead_lag=LeadLagParams(                     # FROZEN 2026-07-22 (G3)
        divergence_threshold=0.02,
        confirmation_snapshots=3,
        convergence_window_s=300,
        min_divergences=30,
    ),
    bounded_verdict_date="2026-09-30",         # FROZEN 2026-07-22 (G4)
    db_path=str(_DATA / "db" / "pmk.db"),
    raw_dir=str(_DATA / "raw"),
    artifacts_dir=str(_DATA / "artifacts"),
    decisions_path=str(ROOT / "sports" / "lol" / "DECISIONS.md"),
)
