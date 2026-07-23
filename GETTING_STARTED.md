# GETTING_STARTED.md

A practical checklist for the first working session. Do these in order;
stop at each gate for human review.

## 0. Environment

```bash
cd pmarket-kalshi-research
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # fill values; NEVER commit .env
pytest -q               # everything (stubs) should collect; leakage canary green
```

Verify API keys by length/hash, not by printing them. Public Polymarket
and Kalshi market-data reads may need no key.

## 1. Validate vendors BEFORE spending (skill: vendor survival)

For each data source, with a **throwaway key**:
- Pull one known market/result and pin the actual response schema into
  `DECISIONS.md` (a NOTE entry). Don't trust the docs; trust the pull.
- Read quota semantics — daily vs **lifetime** budgets. Circuit-break on
  429s; never blind-retry a hint-less 429.
- Confirm Polymarket book ladder ordering empirically (worst→best?),
  record it.
- Archive raw vendor pages verbatim under `data/<sport>/raw/` so a plan
  downgrade can't take your data with it.

## 2. Freeze thresholds (skill: governance before code)

Open the sport's ledger `sports/<sport>/DECISIONS.md`. For each threshold,
write a dated FREEZE entry with the **exact population** it applies to,
before running anything that the threshold would judge. Proposed defaults
live in `sports/<sport>/params.py` (global constants in `core/config.py`)
and are NOT frozen until copied into that sport's ledger. Each sport freezes
its OWN numbers on its OWN first real run — never inherits another sport's.

## 3. Build order (each ends at a gate)

Run any gate for a sport with `python -m engine.run --sport <sport> --gate GN`.

1. **Ingest + store** (`core/ingest/`, `core/db/`) with the leakage canary
   green from day one. Point-in-time reads only via `core/db/store.py`.
2. **G0 — feasibility census** (`core/census/`, population predicate from
   `sports/<sport>/population.py`). Classify from text; depth at
   signal-moments; tier-1 coverage vs the neutral schedule. → human review.
3. **G1 — settlement parity** (`core/parity/settlement.py`). Same-claim per
   family; exclude failures. → human review.
4. **G2 — calibration** (`core/reference/calibration.py`). Per regime,
   per venue, vs neutral outcomes. → human review.
5. **G3 — lead-lag** (`core/reference/lead_lag.py`). Per regime, on diverged
   pairs, seeded event-block bootstrap. → human review.
6. **G4 — verdict** (`core/analysis/report.py`). Verdict-first, with n, CIs,
   caveat flags. Bounded date if sample short.

## 5. Adding a new sport

Confined to `sports/<sport>/` + `data/<sport>/`: `population.py`,
`outcomes.py`, `params.py` (proposed, unfrozen), `DECISIONS.md` (open the
ledger), and a `Sport` class registered in `sports/__init__.py`. The core
engine, store, stats, and tests are reused unchanged.

## 4. Discipline reminders

- `pytest -q` before and after every change.
- Never write execution/wallet code.
- Never touch a frozen threshold; add a dated ruling instead.
- Gaps are information — never interpolate or fabricate.
- Grade outcomes against the neutral source, never venue settlement.
