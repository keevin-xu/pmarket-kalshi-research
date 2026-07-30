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
    poll_interval_s: int = 60          # 60s: fine for a 5-min convergence window
    tier1_only: bool = True
    catalog_ttl_s: int = 300
    cooldown_s: int = 3600
    max_markets_per_cycle: int = 400
    # When the catalog exceeds the cap the selection ROTATES between cycles.
    # A fixed head-slice would poll the same first N markets forever and never
    # once look at the tail — a permanent blind spot that looks like a healthy
    # recorder in the logs.
    # A market whose book 404s is skipped for this many cycles. Illiquid
    # "open" markets have no book at all and otherwise burn most of the request
    # budget, which is what forces the cap to bind in the first place. Kept
    # short so a market that gains a book near kickoff is picked up quickly.
    nobook_cooldown_cycles: int = 5
    # Kalshi orderbook full-book parse UNVERIFIED until a live match pins it.
    kalshi_orderbook_verified: bool = False
    # Order-book archival: "compact" keeps the FULL ladder ([[price,size],...]
    # per side) minus vendor metadata (~1/2-1/3 the bytes; preserves the
    # price-impact curve over time); "full" keeps the verbose vendor JSON;
    # "none" keeps only the parsed columns (mid + top/total depth).
    book_archive: str = "compact"
    # Disk guard: if free space on the DB's filesystem drops below this, the
    # recorder forces archive="none" for the cycle and logs LOUDLY, so a full
    # disk never corrupts the DB. Parsed columns keep recording safely.
    min_free_disk_mb: int = 1024


@dataclass(frozen=True)
class BackfillConfig:
    # One-shot capture of vendor history into the local store. MECHANICS only
    # (pacing, retries, how much series to keep around a map) — nothing here
    # is a judged threshold.
    # Neither venue publishes a rate-limit or Retry-After header, so pacing is
    # self-imposed. Kalshi refused at ~7 req/s during recon and was clean at 1.
    kalshi_min_interval_s: float = 0.35
    polymarket_min_interval_s: float = 0.35
    bo3_min_interval_s: float = 1.0
    # A refusal (429/5xx/transport) is an outage, never "no data": back off,
    # retry a bounded number of times, then FAIL the stage loudly.
    max_refusal_retries: int = 4
    refusal_backoff_s: float = 15.0
    candle_period_min: int = 1               # Kalshi candles: 1-minute bars
    preroll_s: int = 6 * 3600                # keep series from 6h before a map
    postroll_s: int = 900                    # ...to 15 min after it ends


@dataclass(frozen=True)
class CoreConfig:
    regimes: Regimes = field(default_factory=Regimes)
    venues: Venues = field(default_factory=Venues)
    bootstrap: BootstrapConfig = field(default_factory=BootstrapConfig)
    recorder: RecorderConfig = field(default_factory=RecorderConfig)
    backfill: BackfillConfig = field(default_factory=BackfillConfig)

    # Venue endpoints from env (no secrets baked in). Venue MECHANICS, global.
    polymarket_gamma: str = os.environ.get("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com")
    polymarket_clob: str = os.environ.get("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com")
    polymarket_data: str = os.environ.get("POLYMARKET_DATA_BASE", "https://data-api.polymarket.com")
    kalshi_base: str = os.environ.get("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")


CONFIG = CoreConfig()
