# pmarket-kalshi-research

Measurement project: **is Kalshi a usable price reference (calibrated +
leading) for trading tier-1 League of Legends on Polymarket?** Not a
trading bot — the deliverable is a go/no-go verdict. A clean "no" is a
success.

New here? Read in this order:
1. `CLAUDE.md` — hard invariants (non-negotiable rules).
2. `DECISIONS.md` — the append-only ledger (the actual law/history).
3. `SPEC.md` — what we're building and the gates.
4. `docs/METHODOLOGY.md` — calibration + lead-lag, in depth.
5. `docs/SYSTEM_OVERVIEW.md` — how the code fits together.

## Layout

```
pmarket-kalshi-research/
├── CLAUDE.md DECISIONS.md SPEC.md README.md GETTING_STARTED.md
├── config.py            # ALL tunables (nothing frozen until DECISIONS says so)
├── conftest.py          # pytest fixtures (mock adapters only)
├── db/                  # point-in-time SQLite store + schema
│   ├── schema.sql
│   └── store.py         # the one ts<asof read helper — use nothing else
├── ingest/              # venue + outcome adapters (mock/replay by default)
│   ├── base.py          # UTC + fixed-width ts + idempotent upsert
│   ├── polymarket.py    # Gamma/CLOB/Data
│   ├── kalshi.py        # trade-api v2
│   ├── outcomes.py      # NEUTRAL results (Oracle's Elixir / PandaScore)
│   └── record.py        # live dual-venue recorder (cursors persisted)
├── parity/settlement.py # GATE: same-claim mapping per market family
├── census/              # GATE: population classify + depth-at-signal-moment
│   ├── population.py
│   └── depth.py
├── reference/           # THE CORE
│   ├── calibration.py   # is the venue's price true? (per regime)
│   └── lead_lag.py      # does it get there first? (per regime)
├── analysis/            # metrics (bootstrap) + verdict-first reporting
│   ├── metrics.py
│   └── report.py
├── engine/run.py        # orchestrates census→parity→calibration→leadlag→report
├── data/                # raw/ (verbatim archives), db/ (sqlite), artifacts/
└── tests/               # incl. test_leakage.py (canary — green every commit)
```

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # fill; never commit
pytest -q                 # leakage canary must be green
python -m engine.run --help
```

## The one thing to remember

Two gates guard every cross-venue number: a venue isn't a **reference**
until it passes **calibration + lead**, and no number crosses venues
until **settlement parity** proves the same claim. Skipping either
produces confident, wrong verdicts.
