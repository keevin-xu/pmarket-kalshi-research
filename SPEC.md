# SPEC.md — `pmarket-kalshi-research`

**Version:** 0.1 (scaffolding). Nothing here is frozen until it is
copied into `DECISIONS.md` with a date. This file is the design; the
ledger is the law.

---

## 1. The question

Is **Kalshi** a usable price **reference** for trading **tier-1 League
of Legends** on **Polymarket**? "Usable reference" decomposes into two
independent properties, measured **per regime**:

1. **Calibration** — do the venue's prices, read as probabilities,
   match realized outcome frequencies? (Is its number *true*?)
2. **Lead-lag** — when the two venues diverge, which one does the other
   converge toward before close? (Does it get there *first*?)

A reference is usable only if it is **both** calibrated **and** leading
the venue we intend to trade. Calibration without lead means the truth
arrives too late to trade; lead without calibration means we'd converge
toward a fast-but-wrong book (negative CLV). Kalshi must win both to be
trusted; if Polymarket wins instead, the trade direction flips (see §7).

**Scope:** tier-1 leagues broadly (LPL / LEC / LCK / LCS / LCP +
internationals). Execution intent is **Polymarket** (see §7 for the
vice-versa case). We do not trade in this repo.

## 2. Regimes (measured separately — leadership can flip between them)

- **Pre-match:** from market open to map/series start. Snapshot for
  calibration = **the price at kickoff (pre-match close)**.
- **In-game:** during a live map/series. Snapshot for calibration = a
  **fixed, pre-registered in-game checkpoint** (a defined game state,
  e.g. first-blood / first-tower / a set game-clock), **never the last
  tick before resolution** (which collapses into the outcome).

## 3. Populations (pre-register precisely before measuring)

- **Market families:** series-winner and map-winner first. Handicaps /
  totals / exact-score are later, gated on the depth census.
- **Exclude props** ("any player penta kill?") entirely — they poison
  every naive statistic and are not the trade.
- **Tier-1 classification** from question/event text with
  word-boundary code matching + substring name matching, **exclusion
  list checked first** ("LCK Challengers" ≠ "LCK").
- **Coverage:** a match enters the population only if both venues list a
  parity-passing contract for it AND the neutral source has its result.

## 4. Gates (each ends at a human review; order matters)

| Gate | Name | Pre-registered pass criterion (TBD → `DECISIONS.md`) |
|------|------|------------------------------------------------------|
| G0 | Feasibility census | Both venues list tradeable LoL winner markets; depth at signal-moments ≥ $X/side; props filtered; tier-1 coverage ≥ Y% vs neutral schedule. |
| G1 | Settlement parity | ≥ Z% of matched contracts pass same-claim parity per family; failing families excluded, not fudged. |
| G2 | Calibration | On resolved markets, per regime, measure calibration error (e.g. Brier / reliability-curve deviation) for each venue. Pass = Kalshi's error ≤ Polymarket's within a pre-registered margin. |
| G3 | Lead-lag | Per regime, on diverged pairs, measure net convergence direction with a seeded event-block bootstrap. Pass = one venue leads with CI excluding zero. |
| G4 | Verdict | Combine: is there a (regime, direction) where the leader is also the calibrated reference and the follower has depth to trade? Go/no-go, bounded verdict date. |

**Thresholds (`X`, `Y`, `Z`, margins, CI level) are frozen in
`DECISIONS.md` before the data to judge them exists.** Not here.

## 5. Method detail

### 5.1 Calibration (`core/reference/calibration.py`)
- Input: resolved markets, each with `(venue, regime, snapshot_price,
  realized_outcome∈{0,1})`. Outcome from the **neutral** source.
- Bucket by price; compute realized YES-rate per bucket; compare to the
  bucket price. Summaries: reliability curve, Brier score, calibration
  error (e.g. ECE), log-loss. Report per venue, per regime.
- CIs by **event-block bootstrap** (all contracts of one match in one
  block), seeded.

### 5.2 Lead-lag (`core/reference/lead_lag.py`)
- Input: aligned price **time-series** for both venues per market.
- Detect divergences (gap ≥ threshold, confirmed, not flicker).
- For each divergence, measure which venue the other converges toward
  over the following window (and by how much). Aggregate signed
  convergence; positive = Kalshi leads (config-defined sign).
- Also report a symmetric lead-lag cross-correlation / Granger-style
  check as a corroborator. Per regime. Event-block bootstrap, seeded.

### 5.3 Settlement parity (`core/parity/settlement.py`)
- Per market family, an offline mapping test: same map count / series
  length, same void vs resolve rules (forfeits, no-shows, unplayed
  legs), same resolution source & timing. A family passes only with a
  stored test. Non-passing families are excluded from G2–G4.

### 5.4 Depth census (`core/census/depth.py`)
- Depth at the **moments a signal would fire**, per venue, per regime.
  Books deepen near events; a random-instant median lies. Volume ≠
  depth. Feeds G0 and the capacity note in the verdict.

## 6. Data sources

| Role | Source | Notes |
|------|--------|-------|
| Polymarket live/hist | Gamma (discovery), CLOB `/book` & `/prices-history`, Data API | book ladders may arrive worst→best; pin ordering empirically. |
| Kalshi live/hist | Kalshi trade-API v2 (markets, orderbook, candlesticks) | order book → use mid/last, NO de-vig. |
| Neutral outcomes | Oracle's Elixir (CSV) and/or PandaScore results | the answer key; never a venue's own settlement. |
| Historical archives (optional) | polymarketdata.co, Kalshi archive (Lychee/EntityML) | validate with throwaway key before purchase; archive raw pages verbatim. |

## 7. The vice-versa case (build it symmetric)

If G2/G3 find **Polymarket** is the calibrated leader, then Kalshi is
the tradeable laggard and the trade would execute on Kalshi. The
analysis is direction-agnostic and must report leadership per regime.
Executing on Kalshi carries extra costs (KYC, capital lock at
settlement, position limits) — these are **measured capacity costs**,
not folded into the spread haircut, and are out of scope for this
(measurement-only) repo beyond noting them in the verdict.

## 8. Non-goals

- No order placement, no wallets, no execution — any venue.
- No live trading strategy. The output is a verdict about references.
- No claim that a gap = an edge. Gaps are hypotheses; CLV/lead-lag
  graded against validated references and neutral outcomes are the test.
