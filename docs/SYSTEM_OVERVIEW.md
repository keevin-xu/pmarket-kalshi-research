# SYSTEM_OVERVIEW.md — how the code fits together

A narrative map of the pipeline. Data flows top to bottom; each gate is a
human review point. The engine is **sport-agnostic** (`core/`); everything
sport-specific is a plugin (`sports/<sport>/`) behind the `Sport` interface.

```
   sports/<sport>/  ── the plugin: classify_family/is_prop/is_tier1,
   (e.g. sports/lol) discovery (Polymarket tag + Kalshi tickers), neutral
        │            outcomes (load_matches/load_map_results), FROZEN params
        │            (params.py) + its own DECISIONS.md.  engine passes `sport`.
        ▼
            NEUTRAL SCHEDULE / RESULTS  (sport.load_matches — e.g. Oracle's Elixir)
                          │  (the answer key + coverage check)
                          ▼
 core/ingest/polymarket.py ┐      ┌ core/ingest/kalshi.py
    (Gamma/CLOB/Data)      ├─ core/ingest/base.py ─┤ (trade-api v2, mid/last, NO de-vig)
    core/ingest/record.py ─┘  UTC, fixed-width ts, idempotent upsert, cursors
                          │
                          ▼
               core/db/store.py  ── the ONE point-in-time read helper
               core/db/schema.sql   (strict ts < asof; provenance + venue cols)
                          │
        ┌─────────────────┼───────────────────────────┐
        ▼                 ▼                            ▼
 core/census/*        core/parity/settlement.py  core/reference/calibration.py
 (G0: coverage +      (G1: same claim per         (G2: is the price true?
  depth@signal;        market family)              per venue/regime, vs NEUTRAL)
  predicate=sport.*)                                     │
                                                  core/reference/lead_lag.py
                                                  (G3: does it lead? per regime)
                          │
                          ▼
              core/analysis/metrics.py  (event-block bootstrap, seeded)
                          │
                          ▼
              core/analysis/report.py   (G4: verdict-first — verdict, n, CI, flags)
                          ▲
    engine/run.py --sport <s> --gate GN  orchestrates the chain, stops at gates
```

## Module responsibilities

- **`core/sport.py`** — the `Sport` interface + `SportParams`. Every gate
  takes a `sport` and reads `sport.params`; no core module names a sport.
- **`sports/<sport>/`** — the plugin: `population.py` (classify/prop/tier),
  `outcomes.py` (neutral source), `params.py` (FROZEN thresholds),
  `DECISIONS.md` (that sport's ledger), and the `Sport` class in `__init__.py`.
- **`core/db/store.py`** — the single source of point-in-time truth: every
  read via `read_asof(...)` with strict `ts < asof`; provenance + venue cols.
- **`core/ingest/base.py`** — shared adapter contract: raise on naive
  datetimes, fixed-width ISO-8601 UTC, upsert on natural keys, cursors.
- **`core/ingest/{polymarket,kalshi}.py`** — venue adapters (mechanics only;
  the sport is a discovery filter passed in). Kalshi → mid/last, never de-vig.
- **`core/ingest/record.py`** — the observe-only live recorder (one process
  per sport; `--sport <s>`).
- **`core/census/*`** — G0: coverage vs the neutral schedule + depth at
  signal-moments, with the population predicate injected from `sport`.
- **`core/parity/settlement.py`** — G1: same-claim per family.
- **`core/reference/calibration.py` / `lead_lag.py`** — G2/G3 math.
- **`core/analysis/metrics.py` / `report.py`** — bootstrap; G4 verdict.
  Every number traces to a stored artifact under `data/<sport>/artifacts/`.
- **`engine/run.py`** — orchestration + CLI (`--sport`, `--gate`); stops at gates.

## Invariants the leakage canary pins (`tests/test_leakage.py`)

- `read_asof` never returns a row with `ts >= asof`.
- Timestamp encoding is fixed-width so string order == time order.
- Any fitted feature (recalibration curve) rebuilt at an earlier `asof`
  is a prefix-consistent subset — no lookahead.
