# CLAUDE.md — hard invariants for `pmarket-kalshi-research`

Read this and the relevant sport's `sports/<sport>/DECISIONS.md` end-to-end
before writing any code. This file lists rules that must never be violated.
`SPEC.md` says what we're building; each sport's `DECISIONS.md` is its
append-only ledger of pre-registered thresholds and rulings and is that
sport's actual history. Where this file and a sport's `DECISIONS.md`
disagree, the ledger wins for anything it has explicitly ruled on.

This project inherits the `polymarket-edge-finder` methodology
(v1.1). The rules below are the subset that are non-negotiable for
*this* project.

## Repo shape (multi-sport)

A **sport-agnostic `core/` engine** drives **per-sport plugins**
(`sports/<sport>/`, e.g. `sports/lol/`) behind the `Sport` interface
(`core/sport.py`). No module in `core/` names a sport. Each sport owns its
**frozen params** (`sports/<sport>/params.py`), its **own** ledger
(`sports/<sport>/DECISIONS.md`), and its **own isolated data**
(`data/<sport>/`). One sport's frozen numbers NEVER bind another; each
freezes on its own first real run. Gates run per sport:
`python -m engine.run --sport <sport> --gate GN`.

## 0. What this project is

A **measurement system, not a trading bot.** The question:

> Is Kalshi a usable price reference for trading League of Legends on
> Polymarket — i.e. is Kalshi both **calibrated** (its prices match
> realized outcomes) and does it **lead** Polymarket (Polymarket drifts
> toward Kalshi, not vice-versa) — per regime (pre-match, in-game)?

The deliverable is a trustworthy number and a **go/no-go verdict**, not
a strategy. A clean "Kalshi is not a usable reference / there is no
tradeable lead-lag" is a **successful outcome**, not a failure.

We do NOT trade in this repo. We decide whether a later, separate
project *could*.

## 1. Hard refusals (never do these)

- **Never write order-placement, order-signing, wallet, private-key,
  or fund-movement code — for any venue, even as a stub, even if asked
  casually.** Flag it and stop. Execution is a separate repo that only
  exists after gates pass, as a separate human decision.
- **No credentials in the repo, ever.** `.env` only (git-ignored).
  Verify keys by name/length/hash, never by printing their values.
- **Mock/replay adapters are the default** for all dev and tests. No
  test may hit a live vendor.
- **Paper/measurement honesty beats a convenient number.** If a live
  component's honesty and a nicer number conflict, keep the honest one.

## 2. Governance before code

- **Pre-register everything judgeable in the sport's `DECISIONS.md`
  before the data that would be judged by it exists.** Thresholds,
  decision metrics, and the *exact population* they apply to. Date every
  entry. Each sport pre-registers in its OWN ledger.
- **Freeze kill criteria on the first real run.** After that:
  measurement bugs may be fixed; thresholds may not be touched.
  Changing a frozen threshold requires an explicit human ruling
  recorded verbatim, and invalidates prior runs.
- **No early kills, no early passes.** Short sample → set a bounded
  verdict date and judge whatever exists then. The only remedies for
  short n are better measurement or calendar accrual — never relaxing
  a threshold.
- **Corrections are append-only.** A retracted claim gets a dated
  CORRECTION entry; the original stays.

## 3. The two gates that are specific to this project

These are first-class gates. Code that produces a cross-venue number
before these pass is producing confident, wrong numbers.

- **Reference-validation gate.** A venue may not be used as "truth"
  until it passes BOTH calibration and (for lead-lag signals) leads the
  traded venue. See `SPEC.md` §Reference validation and
  `core/reference/`. Grading CLV against an un-validated reference is
  forbidden.
- **Settlement-parity gate.** No number crosses venues until the two
  contracts are proven the *same claim* per market family
  (`core/parity/settlement.py`). A comparison across non-identical claims
  is a mapping bug, not a market result.

## 4. Data invariants (enforced by the leakage canary)

- **Strict `ts < asof` on every read**, through the one shared helper in
  `core/db/store.py`. No ad-hoc date filters in analysis or signal code.
  Anything *fitted* (e.g. a recalibration curve) is also subject to
  this — fitting on data at/after `asof` is the classic silent leak.
- **The leakage canary (`tests/test_leakage.py`) must be green on every
  commit.** It pins as-of semantics, timestamp encoding, and
  rebuild-invariance of fitted features.
- **UTC, timezone-aware, everywhere.** Raise on naive datetimes at
  ingestion boundaries. Store timestamps **fixed-width ISO-8601 UTC** so
  lexicographic order == chronological order in SQL string compares.
- **Provenance + venue columns on every market row**
  (`source='hist'|'live'`, `venue='polymarket'|'kalshi'`). Reads state
  what they want; mixing `hist`/`live` is an explicit opt-in, never a
  default. Any metric over `hist` rows is an upper bound and carries
  that flag into the report. Depth-dependent metrics over historical
  bars are a bug.
- **Gaps are information.** Never interpolate, forward-fill, or
  fabricate market data. A one-sided book is a row missing the other
  side, not a zero. A missing market is a reported failure.
- **Idempotent ingestion**: natural keys + upsert; re-ingesting
  unchanged input changes nothing.
- **All tunables in config, none in code.** Global cross-sport constants
  in `core/config.py`; per-sport frozen thresholds in
  `sports/<sport>/params.py`. A magic number in analysis or signal code
  is a bug. Prices/probabilities live in [0,1]; convert odds at the
  ingestion boundary only.

## 5. Reference math (do not misapply the de-vig)

- **Vigged sharpbook reference (e.g. Pinnacle):** proportional de-vig
  `p_i = q_i / Σq_j` of *raw* odds, identical on hist and live.
- **Order-book venue reference (Kalshi, or Polymarket-as-reference):**
  there is **no vig** — the two sides already sum to ~1. Use the **mid**
  (or last), chosen once in the sport's `params.py`. **Never run an order
  book through the de-vig formula.**
- One method per reference type, across all phases, or the metric isn't
  one metric.

## 6. Ground truth

- **Grade outcomes against a NEUTRAL results source** (Oracle's Elixir
  or PandaScore match results), never a venue's own settlement. Both
  venues are scored against the same independent answer key.
- The neutral source is also how we verify each venue actually *covers*
  a match — join by fuzzy team name within a time tolerance against a
  neutral schedule, never trust a feed's self-reported coverage.

## 7. Determinism & restart safety

- Seed all randomness (bootstraps). A replay/analysis run must be
  bit-identical when run twice (verify by hashing table dumps).
- Persist stream cursors for any live recorder; commit cursors
  atomically with the rows they cover, so a restart can't re-process
  old data and mint duplicate rows.
- Every discarded evaluation is a stored row with a reason code
  (no quote, inside spread, below depth, failed parity, reference not
  validated, missing outcome…). The discard distribution is a research
  output, never debug noise.

## 8. Working in this repo

- Run `pytest -q` before and after every change; the leakage canary
  must stay green.
- If a vendor API contradicts the spec, adapt the ingest layer, keep
  the internal schema stable, and note it in the sport's `DECISIONS.md`.
- When a verdict is contested, don't argue with the data — write the
  ruling down and follow it.
- Never write execution/wallet code. (Repeated because it matters.)
