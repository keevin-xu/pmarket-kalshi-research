"""CS2 params — PROPOSALS, NOT FROZEN.

Nothing here binds until the matching FREEZE entry is written in
sports/cs2/DECISIONS.md, before the run it would judge. These values bind
ONLY CS2; LoL's numbers are untouched and are never inherited.

Two values are already the subject of dated entries and are carried here as
written there:
  * `in_game_checkpoint_s = 1200` — DECISION 2026-07-30 (approved): the
    in-game snapshot is `bo3.gg games.begin_at + 1200 s`, the same instant on
    both venues. Deliberately NOT 600 (LoL's), and deliberately not halftime,
    which bo3.gg cannot timestamp.
  * `tier1_leagues = ("s",)` — DECISION 2026-07-30: the field carries the
    NEUTRAL SOURCE's tier codes, not league or tournament names.
"""
from __future__ import annotations

from core.config import ROOT
from core.sport import (CensusParams, LeadLagParams, ParityParams,
                        ReferenceParams, SportParams)

_DATA = ROOT / "data" / "cs2"

# The secondary, always-reported population (DECISION 2026-07-30). Reported
# beside tier-s in every gate artifact; never substituted for it.
TIER_SECONDARY_ARM = ("a",)

# Validity guard for bo3.gg `games.duration`, which recon found untrustworthy
# row-by-row (aborted maps report 1-3 minutes against 14+ rounds). A map whose
# reported duration is below this many seconds per played round is treated as
# having NO reliable length, so it can never silently satisfy the in-game
# checkpoint's "still in progress" test.
MIN_SECONDS_PER_ROUND = 45

# Neutral source. bo3.gg is unofficial and publishes no rate-limit headers,
# so pacing is self-imposed (recon: clean at 1 req/s).
BO3_API_BASE = "https://api.bo3.gg/api/v1"
BO3_MIN_REQUEST_INTERVAL_S = 1.0
BO3_PAGE_LIMIT = 100

CS2_PARAMS = SportParams(
    key="cs2",
    census=CensusParams(
        min_depth_usd_per_side=250.0,          # proposal (G0)
        min_covered_matches=60,                # diagnostic; the gate is maps+blocks
        # RULING 2026-07-30, amended: the G0 sample floor is counted in the
        # study's own units. A block is a TOURNAMENT, the only unit that
        # addresses the real risk (a result resting on one Major); 6 rather
        # than 8 because the tier-s calendar runs ~4-6 events a quarter, and 8
        # would push the verdict past the bounded date.
        min_covered_maps=100,
        min_event_blocks=6,
        event_block_unit="tournament",
        tier1_coverage_floor=0.80,             # diagnostic only
        families_phase1=("map_winner",),       # match_winner is phase 2
        coverage_join_tolerance_min=90,
        team_match_threshold=0.85,
        tier1_leagues=("s",),                  # NEUTRAL tier codes, not names
        # FROZEN at the G0 FREEZE (2026-07-30) to the OBSERVED Kalshi
        # market-retention floor: the earliest market row the 2026-07-30
        # backfill could retrieve was 2026-05-25T00:07Z. Kalshi drops
        # market-level rows older than ~68 days, so a later pull would find a
        # LATER floor — this value is only reproducible from the archive.
        window_start="2026-05-25T00:00:00.000Z",
    ),
    parity=ParityParams(
        min_family_pass_rate=0.95,             # proposal (G1)
        min_aligned_maps=30,                   # proposal (G1)
    ),
    reference=ReferenceParams(
        orderbook_reference="mid",             # order book -> mid, NEVER de-vig
        in_game_checkpoint_s=1200,             # DECISION 2026-07-30 (approved)
        calibration_pass_margin=0.0,           # proposal (G2)
    ),
    lead_lag=LeadLagParams(                    # proposals (G3)
        divergence_threshold=0.02,
        confirmation_snapshots=3,
        convergence_window_s=300,
        min_divergences=30,
    ),
    bounded_verdict_date="",                   # set at the G4 freeze
    db_path=str(_DATA / "db" / "pmk.db"),
    raw_dir=str(_DATA / "raw"),
    artifacts_dir=str(_DATA / "artifacts"),
    decisions_path=str(ROOT / "sports" / "cs2" / "DECISIONS.md"),
)
