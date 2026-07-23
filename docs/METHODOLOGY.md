# METHODOLOGY.md — calibration & lead-lag, in depth

This is the conceptual core. Code in `core/reference/` implements it.

## Why two tests, and why neither alone is enough

We want to know if Kalshi's price can serve as "the truth" we grade
Polymarket entries against. A price can fail that job two different ways,
so we need two independent tests:

- **Calibration** answers *is the number true?* A venue is calibrated if,
  across many markets, its 60¢ contracts win ~60% of the time, its 70¢
  win ~70%, etc. Miscalibrated prices are lies even if they move first.
- **Lead-lag** answers *does it get there first?* When the two venues
  disagree, the follower drifts toward the leader before close. Only the
  follower is tradeable; only the leader is a usable reference.

The trap that forces both: a **fast-but-wrong** venue. A jumpy book that
overreacts to every rumor "leads" on lead-lag — the calmer venue
partially follows it — but it's dragging the follower toward
mispricings. Trade on that and you get negative CLV. Calibration is the
only test that catches it. Symmetrically, a venue can be perfectly
calibrated but *slow*, so its truth arrives after the close — useless for
trading. **The usable reference is accurate AND early.**

## Calibration — how

1. Take resolved markets. For each, snapshot the venue price at ONE
   fixed, pre-registered point-in-time (see "snapshot choice"), and pair
   it with the realized outcome (0/1) from the **neutral** results
   source — never the venue's own settlement.
2. Pool many markets (all teams, all matches). Bucket by price.
3. In each bucket, compute realized YES-rate; compare to bucket price.
4. Summarize: reliability curve, Brier score, expected calibration
   error (ECE), log-loss — per venue, per regime.
5. CIs by **event-block bootstrap** (all contracts of one match in one
   block; trades within a match aren't independent), seeded.
6. **Pass:** Kalshi's calibration error ≤ Polymarket's within the
   pre-registered margin (frozen in `DECISIONS.md`).

**Snapshot choice (critical — avoids the outcome-leak trap):**
- Pre-match regime → price **at kickoff** (pre-match close). All
  pre-game info is in; zero game state has leaked. The honest test.
- In-game regime → a **fixed game-state checkpoint** (e.g. a set
  game-clock or objective). **Never the last tick before resolution** —
  near settlement the winner's price runs to ~99¢ and *every* venue
  looks perfectly calibrated because it's just reading the scoreboard.
- Use the **same instant for both venues**; mixing instants is a leak.

Calibration is NOT "who picks more winners." A book that always slaps
99¢ on the favorite picks lots of winners but is badly calibrated. We
check the confidence is right at every price level.

## Lead-lag — how

1. Align both venues' price **time-series** for a market (needs full
   series, not the single calibration snapshot).
2. Detect **divergences**: gap ≥ a pre-registered threshold, *confirmed*
   (a small state machine, not a one-tick flicker).
3. For each divergence, over the following pre-registered window,
   measure which venue the **other** converges toward, and by how much.
   Signed aggregate: define the sign so positive = Kalshi leads.
4. Corroborate with a symmetric cross-correlation / Granger-style lag
   check.
5. Per regime (leadership can flip between pre-match and in-game).
   Event-block bootstrap, seeded.
6. **Pass:** one venue leads with the bootstrap CI excluding zero.

**The regime caveat that mirrors the map-end repricer failure:** in-game,
both venues watch the same visible game state (gold, towers, Barons). If
both makers are competent they may reprice together within seconds — no
tradeable lag, however large the instantaneous gap looks. The whole
question is whether two *separate order books* with different crowds have
a measurable, tradeable-duration lag. If the reprice completes before a
trigger could fire, there is no lag edge; any edge must then come from
miscalibration, and we say so.

## Combining into the verdict

There is a usable reference (and a trade) only where, **in the same
regime**, one venue is BOTH the calibrated one AND the leader, AND the
follower has depth at signal-moments (from the census). Report per
regime; if Polymarket is the leader, the trade flips to Kalshi (with its
extra execution costs noted, out of scope here).

## Optional: recalibration curve (later, out-of-sample only)

If a venue is calibrated-ish but stably biased (its 70¢ ≈ 65% true), a
learned map `price → true prob` can de-bias it before grading. It's a
**fitted feature** → subject to strict `ts < asof` and the leakage
canary; on thin esports samples it overfits easily. Default to the
pass/fail gate; treat the curve as later polish, never a way to rescue a
failing reference.
