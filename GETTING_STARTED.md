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
- Archive raw vendor pages verbatim under `data/raw/` so a plan
  downgrade can't take your data with it.

## 2. Freeze thresholds (skill: governance before code)

Open `DECISIONS.md`. For each unchecked box in "TO FREEZE", write a
dated FREEZE entry with the **exact population** it applies to, before
running anything that the threshold would judge. Proposed defaults live
in `config.py` and are NOT frozen until copied here.

## 3. Build order (each ends at a gate)

1. **Ingest + store** (`ingest/`, `db/`) with the leakage canary green
   from day one. Point-in-time reads only via `db/store.py`.
2. **G0 — feasibility census** (`census/`). Classify population from
   text; measure depth at signal-moments both venues; check tier-1
   coverage vs the neutral schedule. → human review.
3. **G1 — settlement parity** (`parity/`). Prove same-claim per family;
   exclude failures. → human review.
4. **G2 — calibration** (`reference/calibration.py`). Per regime,
   per venue, vs neutral outcomes. → human review.
5. **G3 — lead-lag** (`reference/lead_lag.py`). Per regime, on diverged
   pairs, seeded event-block bootstrap. → human review.
6. **G4 — verdict** (`analysis/report.py`). Verdict-first, with n, CIs,
   caveat flags. Bounded date if sample short.

## 4. Discipline reminders

- `pytest -q` before and after every change.
- Never write execution/wallet code.
- Never touch a frozen threshold; add a dated ruling instead.
- Gaps are information — never interpolate or fabricate.
- Grade outcomes against the neutral source, never venue settlement.
