# pmarket-kalshi-research

Measurement project: **is Kalshi a usable price reference (calibrated +
leading) for trading tier-1 League of Legends on Polymarket?** Not a
trading bot — the deliverable is a go/no-go verdict. A clean "no" is a
success.

Multi-sport: a sport-agnostic `core/` engine drives per-sport plugins
(`sports/<sport>/`). LoL is the first sport; CS2/Valorant/cricket plug in
the same way. Each sport has its **own** frozen params, **own** ledger, and
**own** isolated data — LoL's numbers never bind another sport.

New here? Read in this order:
1. `CLAUDE.md` — hard invariants (non-negotiable rules).
2. `sports/<sport>/DECISIONS.md` — that sport's append-only ledger (the law/history).
3. `SPEC.md` — what we're building and the gates.
4. `docs/METHODOLOGY.md` — calibration + lead-lag, in depth.
5. `docs/SYSTEM_OVERVIEW.md` — how the code fits together.

## Layout

```
pmarket-kalshi-research/
├── CLAUDE.md SPEC.md README.md GETTING_STARTED.md
├── conftest.py              # pytest fixtures (mock adapters only)
│
├── core/                    # SPORT-AGNOSTIC engine — never names a sport
│   ├── config.py            # GLOBAL tunables (seed, CI, endpoints, regimes, venues)
│   ├── sport.py             # the Sport interface + SportParams (frozen-shape)
│   ├── db/        store.py schema.sql   # ts<asof point-in-time store
│   ├── ingest/    base.py polymarket.py kalshi.py record.py
│   ├── census/    coverage.py depth.py sweep.py     # population predicate injected
│   ├── parity/    settlement.py                     # family mapping injected
│   ├── reference/ calibration.py lead_lag.py calib_data.py leadlag_data.py
│   └── analysis/  metrics.py report.py
│
├── sports/
│   ├── __init__.py          # REGISTRY = {"lol": LolSport(), ...}
│   └── lol/
│       ├── __init__.py      # LolSport(Sport) — wires the pieces below
│       ├── params.py        # FROZEN LoL thresholds + population
│       ├── population.py    # classify_family / is_prop / is_tier1
│       ├── outcomes.py      # Oracle's Elixir adapter (neutral answer key)
│       └── DECISIONS.md     # LoL's append-only ledger
│
├── data/lol/  db/ raw/ artifacts/     # one ISOLATED tree per sport
├── engine/run.py            # CLI: run --sport lol --gate G0..G4
├── ops/                     # templated recorder unit (pmk-recorder@<sport>)
└── tests/                   # incl. test_leakage.py (canary — green every commit)
```

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                          # fill; never commit
pytest -q                                     # leakage canary must be green
python -m engine.run --sport lol --gate G0    # each gate stops for review
python -m core.ingest.record --sport lol      # observe-only live recorder (LoL)
```

## The one thing to remember

Two gates guard every cross-venue number: a venue isn't a **reference**
until it passes **calibration + lead**, and no number crosses venues
until **settlement parity** proves the same claim. Skipping either
produces confident, wrong verdicts.
