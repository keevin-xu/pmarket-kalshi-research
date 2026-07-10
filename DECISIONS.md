# DECISIONS.md — append-only ledger

**This file is the law.** Every threshold, decision metric, and
population is pre-registered here *before* the data that would be judged
by it exists. Entries are append-only and dated. Measurement bugs may be
fixed; **frozen thresholds are never edited** — a change requires a new
dated ruling that explicitly states it invalidates prior runs.
Corrections are new CORRECTION entries; originals stay.

Format: `[YYYY-MM-DD] TYPE — summary`, then detail. TYPE ∈
{DECISION, RULING, CORRECTION, FREEZE, NOTE}.

---

## [2026-07-09] NOTE — Ledger opened

Project `pmarket-kalshi-research` created to answer: is Kalshi a usable
price reference (calibrated + leading) for trading tier-1 LoL on
Polymarket, per regime? Inherits `polymarket-edge-finder` methodology
v1.1. Scaffolding only; nothing frozen yet.

## [2026-07-09] DECISION — Governing disciplines (from the skill)

1. Measurement, not trading. No execution/wallet code, any venue.
2. Pre-register thresholds + population before the data exists.
3. Two project-specific gates are first-class: **reference validation**
   (calibration AND lead) and **settlement parity** (same claim).
4. Neutral ground-truth outcomes only; never a venue's own settlement.
5. Order-book references use mid/last, NOT de-vig.

## [2026-07-09] DECISION — Regimes measured separately

Pre-match and in-game are analyzed independently; leadership may differ
between them. Calibration snapshot = pre-match close (pre-match regime)
or a fixed in-game checkpoint (in-game regime), never the last tick
before resolution.

---

## TO FREEZE BEFORE FIRST REAL RUN (placeholders — DO NOT treat as frozen)

These are the knobs that must get a dated FREEZE entry, each with its
exact population, *before* the run that judges them. Listed here as a
checklist, with proposed defaults in `config.py` that are **not yet
frozen**.

- [ ] **G0 census:** min depth per side at signal-moment (`$X`);
      tier-1 coverage floor vs neutral schedule (`Y%`); prop-exclusion
      rule; exact market-family list for phase 1.
- [ ] **G1 parity:** fraction of matched contracts that must pass
      same-claim parity per family (`Z%`); the per-family mapping tests.
- [ ] **G2 calibration:** the calibration-error metric (Brier / ECE /
      reliability deviation), the price-bucketing, and the pass margin
      (how much better/no-worse Kalshi must be than Polymarket), per
      regime. The fixed in-game checkpoint definition.
- [ ] **G3 lead-lag:** divergence threshold, confirmation rule,
      convergence window, the aggregate lead statistic, CI level, and
      the pass rule (CI excludes zero). Per regime.
- [ ] **G4 verdict:** the combination rule and the **bounded verdict
      date**.
- [ ] **Sample-size floors** per gate and the calendar-accrual plan if
      short.

## Open questions (resolve into DECISIONs before they bind anything)

- Which neutral outcome source is primary (Oracle's Elixir vs
  PandaScore), and the fuzzy-join tolerance for coverage checks.
- Kalshi contract identifiers per LoL family and their exact settlement
  wording (feeds G1).
- Whether any historical archive is purchased, and the
  throwaway-key validation result (pin the schema here on first pull).
