# SYSTEM_OVERVIEW.md — how the code fits together

A narrative map of the pipeline. Data flows top to bottom; each gate is a
human review point.

```
            NEUTRAL SCHEDULE / RESULTS (Oracle's Elixir / PandaScore)
                          │  (the answer key + coverage check)
                          ▼
  ingest/polymarket.py ┐        ┌ ingest/kalshi.py
   (Gamma/CLOB/Data)   ├─ ingest/base.py ─┤  (trade-api v2, mid/last, NO de-vig)
   ingest/record.py  ──┘  UTC, fixed-width ts, idempotent upsert, cursors
                          │
                          ▼
                   db/store.py  ── the ONE point-in-time read helper
                   db/schema.sql   (strict ts < asof; provenance + venue cols)
                          │
        ┌─────────────────┼───────────────────────────┐
        ▼                 ▼                            ▼
 census/population.py  parity/settlement.py     reference/calibration.py
 census/depth.py       (GATE G1: same claim     (GATE G2: is the price true?
 (GATE G0: population,  per market family)        per venue, per regime,
  depth@signal-moment)                            vs NEUTRAL outcomes)
                                                        │
                                                 reference/lead_lag.py
                                                 (GATE G3: does it lead?
                                                  per regime, diverged pairs)
                          │
                          ▼
              analysis/metrics.py  (event-block bootstrap, seeded;
                                    calibration error, lead statistics)
                          │
                          ▼
              analysis/report.py   (GATE G4: verdict-first —
                                    verdict, n, CI, caveat flags)
                          ▲
              engine/run.py orchestrates the whole chain and stops at gates
```

## Module responsibilities

- **`db/store.py`** — the single source of point-in-time truth. Every
  read goes through `read_asof(...)` with strict `ts < asof`. No other
  module writes ad-hoc date filters. Encodes provenance (`hist`/`live`)
  and `venue`.
- **`ingest/base.py`** — shared adapter contract: raise on naive
  datetimes, store fixed-width ISO-8601 UTC, upsert on natural keys,
  persist cursors atomically. All live vendor calls are mockable.
- **`ingest/{polymarket,kalshi}.py`** — venue adapters. Kalshi is an
  order book → reference price is mid/last, never de-vigged.
- **`ingest/outcomes.py`** — the neutral answer key and coverage source.
- **`census/`** — G0: classify the market population from text (exclude
  props), and measure depth at the moments a signal would fire.
- **`parity/settlement.py`** — G1: prove Kalshi and Polymarket contracts
  are the same claim per family, with stored per-family tests.
- **`reference/calibration.py`** — G2: bucket-by-price reliability,
  Brier/ECE, per venue per regime, vs neutral outcomes.
- **`reference/lead_lag.py`** — G3: divergence detection + signed
  convergence direction, per regime.
- **`analysis/metrics.py`** — seeded event-block bootstrap and the
  aggregate statistics both gates report.
- **`analysis/report.py`** — G4: verdict against the frozen rule first,
  then number, n, CI, caveat flags; every number traces to a stored
  artifact under `data/artifacts/`.
- **`engine/run.py`** — orchestration + CLI; stops at each gate.

## Invariants the leakage canary pins (`tests/test_leakage.py`)

- `read_asof` never returns a row with `ts >= asof`.
- Timestamp encoding is fixed-width so string order == time order.
- Any fitted feature (recalibration curve) rebuilt at an earlier `asof`
  is a prefix-consistent subset — no lookahead.
