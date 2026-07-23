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

---

## [2026-07-12] DECISION — Neutral outcome source + Kalshi auth

- **Neutral ground-truth source = Oracle's Elixir** (free CSV download, no
  key). It is the answer key for G2 and the schedule spine for G0 coverage.
  PandaScore is not used unless a later dated ruling adds it.
- **No Kalshi API key to start.** Kalshi market-data reads are public;
  a key (RSA key-id + private-key file) is deferred to the live-recorder
  phase or if an endpoint proves auth-gated. Recon below used no key.

## [2026-07-12] NOTE — Vendor recon (first pull; validate before trusting)

First live pull, no keys, un-judged (existence + schema only; no coverage
or depth statistic computed — those are G0's judged numbers and are frozen
below before they exist).

**Polymarket (Gamma `/events?tag_slug=league-of-legends`)** — HTTP 200.
LoL present, e.g. `lol-lck-2026-season-winner` ("LCK 2026 Season Winner",
a future). Markets nest in events; event title carries league context.

**Kalshi (`/series?category=Sports`, `/events?series_ticker=…`)** — HTTP
200, no auth. LoL present with the phase-1 families:
- `KXLOL` — "League of Legends" (series/match winner)
- `KXLOLMAP` — "League of Legends Map Winner" (map winner)
- `KXLOLTOTAL`, `KXLOLTOTALMAPS`, `KXLOLGAMES` — totals/maps (LATER families)
- `KXLEAGUEWORLDS`, `KXLEAGUE` — Worlds/tournament futures
- `KXLCKAHRIPICK` — champion-pick PROP (excluded)
Only open KXLOL event at pull time: `KXLOL-MSI26` (MSI-champion future, one
market per team, bid/ask/last = None → no live liquidity this instant).
Market schema keys pinned: `yes_bid_dollars`, `yes_ask_dollars`,
`no_bid_dollars`, `no_ask_dollars`, `last_price_dollars` (all 0–1 →
**mid, NO de-vig**), `yes_bid_size_fp`, `yes_ask_size_fp`,
`liquidity_dollars`, `volume_fp`, `open_interest_fp`, `result`, `status`,
`open_time`, `close_time`, `expiration_time`, `rules_primary`,
`rules_secondary` (settlement text → feeds G1 parity).

**Oracle's Elixir** — downloads page HTTP 200 (reachable).

CAVEAT carried forward: at the instant of recon, live open LoL markets are
tournament futures, not match series-winners. The historical census must
run over *settled* match-level markets; live depth is a separate,
recorder-phase measurement. Existence of the families is confirmed; their
match-level liquidity is a G0 finding, not yet measured.

## [2026-07-12] FREEZE — G0 feasibility census (kill criteria + exact population)

Frozen before any G0 coverage/depth statistic exists. Proposed defaults in
`config.py` are hereby bound for G0 with the population below. Per project
law: measurement bugs may be fixed after the first run; **these thresholds
may not be moved** without a new dated ruling that invalidates prior runs.

**Population (exact):**
- Neutral schedule/results = Oracle's Elixir. Measurement window =
  **2025-01-01T00:00:00Z through the G0 run date** (a match's Oracle's
  Elixir scheduled start `∈` window).
- Tier-1 leagues = codes {LCK, LPL, LEC, LCS/LTA, LCP} + internationals
  {MSI, Worlds}, matched by `census/population.py` (word-boundary code +
  substring name, EXCLUSIONS checked first; "LCK Challengers" ≠ "LCK").
- Phase-1 families = {`series_winner`, `map_winner`} ONLY. Props excluded
  (`is_prop`). Totals/handicaps/exact-score deferred to a later phase.
- A match is **covered** iff BOTH venues list a phase-1-family contract
  joined to the Oracle's Elixir match by fuzzy team-name match within
  **±90 min** (`census.coverage_join_tolerance_min`) of scheduled start,
  AND Oracle's Elixir has its result.

**Kill criteria (ALL must hold to pass G0):**
1. **Existence:** each venue lists ≥1 tradeable contract in each phase-1
   family within the window.
2. **Coverage:** covered-match fraction vs the tier-1 Oracle's Elixir
   schedule **≥ 80%** (`census.tier1_coverage_floor`). Reported alongside
   the absolute covered-match count `n_cov` as a sample-size corroborator.
3. **Depth:** median top-of-book depth **per side ≥ $250**
   (`census.min_depth_usd_per_side`) at signal-moments, measured on the
   **venue we intend to trade (Polymarket)**, per regime. Historical bars
   give an UPPER BOUND on depth and carry that flag; a live/recorder depth
   read supersedes for the binding number.

**Discards:** every excluded market is a stored `discards` row with a
reason code (`prop`, `not_tier1`, `wrong_family`, `no_venue_match`,
`missing_outcome`, `below_depth`). The discard distribution is a G0 output.

Sample-size floor + calendar-accrual plan for a short window: if `n_cov`
is below what G2/G3 need, the only remedies are better measurement or
calendar accrual — never lowering these thresholds.

## [2026-07-12] RULING — G0 coverage criterion is an ABSOLUTE COUNT, not a %

Supersedes kill-criterion #2 of the G0 FREEZE above (no judged G0 run has
occurred, so no prior run is invalidated). Operator decision:

- **G0 coverage passes iff `n_covered_matches ≥ 60`** — the count of
  tier-1 Oracle's Elixir matches (2025-01-01Z → G0 run date) that have a
  phase-1-family contract on BOTH venues (joined ±90 min to scheduled
  start) AND an Oracle's Elixir result.
- Rationale: feasibility needs a big-enough overlap population to run the
  study; it does NOT need either venue to be comprehensive. An 80% floor
  conflated "Kalshi lists most LoL matches" (irrelevant) with "there is
  enough overlap to measure" (what G0 exists to check), risking a false
  kill. The covered-fraction % is still reported as a diagnostic, but the
  **gate is `n_covered ≥ 60`**.
- Existence (crit #1) and depth ≥ $250/side on Polymarket (crit #3) are
  unchanged and still required.

## [2026-07-12] NOTE — Kalshi settled-market recon (census population is real)

Un-judged existence/schema recon (no coverage/depth statistic computed).
- `KXLOLMAP` (map_winner): **200 settled team-vs-team events on page 1,
  cursor present (more pages)**. Structure: one event per map
  (`…-<MATCH>-<mapno>`), two markets (one per team, `yes_sub_title` = team),
  `result ∈ {yes,no}`, `close_time` at map end. Tier-1 and minor teams both
  present (HLE, BLG tier-1; Zeu5, NCG, Fuego, SDM minor) → tier-1 filter is
  load-bearing.
- `KXLOL` (series_winner): **0 settled events** (only the open MSI future).
  FINDING, not a bug: Kalshi's match-level LoL product is the **map winner**,
  not a per-match best-of series winner. Phase-1 on Kalshi is therefore
  map_winner-dominant. Whether a per-match series-winner contract exists
  under another ticker is an open item; series_winner coverage may fail G0
  on the Kalshi side while map_winner passes. Reported per family.

## [2026-07-12] NOTE — Polymarket match-level recon + cross-venue overlap

- Polymarket lists deep per-match LoL events (`/events?tag_slug=league-of-
  legends`), e.g. `lol-blg-hle1-2026-07-12` "BLG vs HLE (BO5) — MSI
  Playoffs" (77 nested markets). Question text drives family:
  "…Game 1 Winner" = **map_winner** (matches `\bgame \d\b`); "(BO5)" /
  "Match Result" = **series_winner** (classifier needs these phrasings —
  Polymarket says "BO5"/"Match Result", not "best of"/"series").
- Market schema: `outcomes`/`outcomePrices` are JSON-string arrays;
  `bestBid`/`bestAsk`/`lastTradePrice` top-of-book; `clobTokenIds` per leg;
  **per-side depth in $ is NOT in Gamma — requires CLOB `/book`** (token id
  → bids/asks with sizes; $ = price×size). Confirms G0 depth on the live/
  recorder path, not Gamma bars.
- **Cross-venue overlap confirmed:** both venues carry the same tier-1 MSI
  matches (BLG vs HLE, G2 vs T1) → a real joint population exists. G0
  feasibility (existence + overlap) is met; the binding numbers (n_covered
  vs Oracle's Elixir, depth medians) remain to be measured and judged.

## [2026-07-12] NOTE — Oracle's Elixir distribution (operator-provided)

The downloads page (`oracleselixir.com/tools/downloads`, HTTP 200) exposes
no direct CSV/Drive link in static HTML — files are shared via a Google
Drive folder requiring manual interaction. **The neutral spine is therefore
an operator download**: drop the per-year `*_LoL_esports_match_data_*.csv`
under `data/raw/`; `ingest/outcomes.py` parses it (team rows → map-grain
`matches`). The JUDGED G0 coverage number (`n_covered ≥ 60` vs the OE
schedule) cannot be produced until that file is present.

## [2026-07-13] RESULT — G0 first real run (thresholds now FROZEN)

First judged G0 run. Per project law, the G0 kill criteria are now FROZEN;
only measurement bugs may be fixed hereafter. OE spine = 2025.csv + 2026.csv.
Artifact: `data/artifacts/G0_20260713T042146Z.json` (sha256 045a8031bdcf2357).

Verdict vs frozen rules:
- **Coverage: PASS.** `n_covered = 115` tier-1 series on BOTH venues
  (gate >= 60). Diagnostic coverage % = 7.3% (115 / 1573 tier-1 series in
  window) — low only because Kalshi's LoL history is short (see below),
  which is exactly why the gate is an absolute count, not a %.
- **Existence: PASS for `map_winner`** (both venues). **`series_winner`
  FAILS on Kalshi** — KXLOL had 0 settled match markets (only a tournament
  future); Kalshi's match-level LoL product is the map winner. series_winner
  is EXCLUDED from G1–G4. Phase-1 proceeds as **map_winner only**.
- **Depth: FAIL (provisional / recorder-pending).** One-shot LIVE Polymarket
  snapshot: top-of-book median **$4/side pre-match** (n=255), **$193.6/side
  in-game** (n=25) — both below the frozen $250/side floor. CAVEAT: this
  snapshot is over whatever LoL markets were open at run time (mixed tier,
  often minor leagues), top-of-book only — indicative, NOT the binding
  tier-1 signal-moment number. The binding depth measurement requires the
  live recorder over tier-1 matches at kickoff.

Findings pinned:
- Kalshi KXLOLMAP settled = 2092 markets, cursor fully exhausted, oldest
  close_time **2026-05-06** → Kalshi LoL launched ~2 months before this run.
  The cross-venue overlap window is therefore ~2026-05 → present; more n is
  a **calendar-accrual** matter, never a threshold change.
- Polymarket match date lives in the event SLUG, not `startDate` (the latter
  is the listing date, median ~6 / max 14 days early); the coverage join
  uses the slug date. Team-name join misses non-derivable aliases (e.g.
  "BLG" for Bilibili Gaming) → coverage is an UNDERcount (conservative).

**Gate ruling:** G0 GO for the **map_winner** family (coverage + existence
pass; population is real). Depth is an open concern to be resolved by the
recorder before any tradeability claim — it does NOT block G1 (settlement
parity), which is not depth-dependent. series_winner is killed. STOP for
human review before G1.

## [2026-07-13] FREEZE — G1 settlement parity (map_winner family)

Frozen before the judged parity run. Applies to the ONLY surviving phase-1
family, **map_winner** (series_winner was killed at G0).

Recon of both venues' settlement semantics (pinned; un-judged):
- **Kalshi KXLOLMAP**: one market per team per map; resolves Yes for that
  map's winner of the specific scheduled match; map number = event-ticker
  suffix (`KXLOLMAP-…HLEBLG-3` → map 3). Source = governing-league result.
- **Polymarket "Game N Winner"**: resolves via UMA against gol.gg / the
  match stream; winner = the outcome priced 1. Source/mechanism differs
  from Kalshi but tracks the same real-world map result.

"Same claim" per family is proven by an empirical, neutral-arbitrated test
over the covered matches:
- **Alignment:** a Kalshi map market and a Polymarket "Game N Winner" market
  are paired iff same fuzzy team-pair, same map number, within ±1 day.
- **Result agreement (the gate):** on aligned maps that Oracle's Elixir
  records as PLAYED, `kalshi_winner == polymarket_winner` (team-name fuzzy).
  **Family passes iff agreement rate ≥ `parity.min_family_pass_rate` = 0.95**
  over aligned played maps, with a minimum of 30 aligned maps (else verdict
  is "insufficient parity sample", judged on the bounded date — never by
  lowering the bar).
- **Neutral corroboration:** report each venue's agreement with the OE map
  winner; a high venue-vs-venue rate with low venue-vs-OE rate is suspect.
- **Void consistency:** a map NOT played per OE (no OE gameid) that a venue
  nonetheless resolved to a team is a void-handling break; both venues must
  void unplayed maps. Any such case is enumerated, not averaged away.
- Every non-agreeing or one-sided/void map is a stored discard with a
  reason code; the disagreement list is a first-class G1 output. Resolution-
  source difference (governing league vs UMA/gol.gg) is a documented caveat,
  not a fail, provided result agreement + void consistency hold.

## [2026-07-13] CORRECTION — G0 coverage 115 -> 72 (measurement-bug fix)

The [2026-07-13] G0 RESULT reported `n_covered = 115`. That number was
inflated by team-name matching bugs found while building G1; the corrected
number is **`n_covered = 72`** (artifact `G0_20260722T012056Z.json`,
sha256 709001382fa2c862). The original entry stands (append-only); this
corrects it. Bugs fixed (measurement only — no frozen threshold moved):
- team normalizer stripped "academy"/"challengers" as noise, collapsing
  secondary squads into their tier-1 parents ("T1 Academy" -> "T1");
- added a secondary-squad guard (academy/youth/challengers/junior/
  development on one side only => different team);
- `map_winner` classification required only "game N", so totals/props
  ("Game 1: Both Teams Slay Baron", "Games Total O/U") leaked in; it now
  requires a winner notion too.
**G0 verdict is UNCHANGED: coverage PASS** (72 >= 60 floor). The honest
number sits comfortably above the floor — the count-based gate holds.

## [2026-07-13] RESULT — G1 settlement parity: PASS (map_winner)

Artifact `G1_20260722T012014Z.json` (sha256 f8b422331f2b232e). Judged vs
the frozen G1 rule.
- **Venue agreement: 189/189 = 100%** on aligned played maps (min 30).
  Kalshi and Polymarket settle the identical winner on every map.
- **Neutral corroboration:** each venue agrees with the Oracle's Elixir map
  winner 100% (kalshi 1.0, polymarket 1.0).
- **Void handling: 0 breaks** — neither venue resolved an unplayed map.
- Resolution-source difference (Kalshi governing-league result vs
  Polymarket UMA/gol.gg) is a documented caveat; it did not affect realized
  settlement on this sample.
**Verdict: PASS — the map_winner contracts are the SAME CLAIM across
venues.** map_winner may proceed to G2 (calibration). STOP for human
review before G2.

## [2026-07-13] FREEZE — G2 calibration (map_winner, per regime)

Frozen before the judged calibration run. Question: is each venue's price,
read as P(team wins the map), TRUE (matches realized frequency), and is
Kalshi at least as calibrated as Polymarket? Population = the G1-passing
map_winner maps (same claim), tier-1, in window. Outcome = Oracle's Elixir
map winner (neutral). Reference price = order-book **mid, NO de-vig**
(config.reference.orderbook_reference). One calibration point per map, per
venue, using the Blue-side team's market: price = P(team_a wins map),
outcome = 1 iff OE says team_a won.

Snapshot point-in-time (fixed, avoids the outcome-leak trap):
- **pre_match:** venue price at the map's KICKOFF = OE map `date` (last
  quote at-or-before kickoff). OE `date` is per-map (verified).
- **in_game:** venue price at **kickoff + 600 s** (a 10-minute game-clock
  checkpoint; `reference.in_game_checkpoint`), for maps whose OE
  `gamelength` >= 600 s. A DEFINED game state, never the last tick.

Metric + gate (per regime, per venue):
- Reliability curve over `reference.calibration_buckets` (0.05..0.95);
  report Brier, log-loss, and **ECE** (the decision metric).
- Event-block bootstrap by series (seeded) for the CI on Brier.
- **PASS iff Kalshi ECE <= Polymarket ECE + `reference.calibration_pass_
  margin` (= 0.0)** — Kalshi must be at least as calibrated as the venue we
  would trade. A regime with < 50 aligned points is "insufficient sample"
  (bounded-date verdict; never lower the bar).
- Corroboration: a venue whose bucket prices systematically miss realized
  rates in one direction is flagged (stable bias → possible later
  recalibration map, fit out-of-sample only; NOT applied at this gate).

## [2026-07-13] RESULT — G2 calibration: comparison INSUFFICIENT SAMPLE; Kalshi computed

Artifact `G2_20260722T015853Z.json` (sha256 307c59c1413deab7). Judged vs the
frozen G2 rule.

- **The cross-venue comparison (the gate) CANNOT be judged: Polymarket
  n = 0.** Root cause is a compounding data constraint, not a bug:
  - Oracle's Elixir CSV (the outcome key) ends **2026-06-14** (download
    vintage); Kalshi LoL history starts 2026-05-16 → Kalshi∩OE outcomes span
    2026-05-16 … 06-14.
  - **Polymarket CLOB `prices-history` serves only ~the last 30 days**
    (verified: every month Jan–Jun 2026 returns EMPTY; only 2026-07 has
    data). Polymarket historical prices are NOT retrievable retroactively.
  - Overlap of {OE-covered maps ≤ 06-14} and {Polymarket-priced maps ≥ ~06-22}
    is **zero** → no paired point exists. Verdict per frozen rule:
    **insufficient sample** (both regimes). Remedies are the two allowed
    ones — better measurement (live recorder to accrue Polymarket prices
    going forward) and calendar accrual — never a threshold change.
- **Kalshi standalone calibration (diagnostic, full candlestick history,
  n=189 maps):** pre_match ECE 0.1125 / Brier 0.2310; in_game ECE 0.1353 /
  Brier 0.2207. Read cautiously — single-map winner is high-variance (a
  coin-flip Brier is 0.25, so ~0.22–0.23 is expected and near-uninformative
  at the map level); ECE ~0.11–0.14 over 19 buckets on 189 points is noisy.
  This says Kalshi *has* a plausible price series to calibrate; it does NOT
  settle the gate (which is the Kalshi-vs-Polymarket comparison).

**Strategic consequence (resolves "recorder vs move on"):** the live
recorder is now REQUIRED, not optional. Polymarket price history cannot be
backfilled beyond ~1 month, so BOTH the G2 cross-venue comparison AND G3
lead-lag depend on recording Polymarket (and Kalshi) prices forward from
now. Also: re-download Oracle's Elixir (current CSV ends 06-14) so outcomes
reach the recorded window. G2 verdict: **pending — bounded-date, recorder-
accrual.** STOP for human review before standing up the recorder / G3.

## [2026-07-22] RESULT — G2 re-run on refreshed OE: PASS (thin sample)

Oracle's Elixir re-downloaded; `2026.csv` now runs to 2026-07-21 (was
06-14). That overlaps Polymarket's rolling ~30-day price-history window, so
a paired sample now exists. Artifact `G2_20260722T185454Z.json`
(sha256 cfd01e32e1c7c3c3).
- **pre_match:** Kalshi ECE 0.1158 (n=253) vs Polymarket ECE 0.1604 (n=64)
  → **PASS** (Kalshi at least as calibrated).
- **in_game:** Kalshi ECE 0.1016 (n=253) vs Polymarket ECE 0.2045 (n=64)
  → **PASS** (Kalshi much better calibrated).
- **Corroborator flag (soft pass):** Polymarket has LOWER Brier (0.206 vs
  0.225 pre; 0.194 vs 0.209 in-game) despite HIGHER ECE — i.e. Polymarket is
  sharper but less calibrated, Kalshi better calibrated but less sharp. The
  ECE gate favors Kalshi; the Brier corroborator disagrees in sign, so this
  is a flagged pass, not a clean one.
- Sample is THIN (Polymarket n=64, just over the 50 floor) and confined to
  the ~1-month OE∩Polymarket-history window; more sample needs recorder
  accrual (Polymarket history evaporates after ~30 days). Supersedes the
  earlier "insufficient sample" for the comparison; the recorder is still
  required to sustain/grow it. STOP for human review before G3.

## [2026-07-22] FREEZE — G3 lead-lag (map_winner, per regime)

Frozen before the judged run. Question: when the two venues DIVERGE, which
one does the other converge toward — i.e. who LEADS? Population = the covered
map_winner maps in the OE∩Polymarket-history overlap (~trailing 30 days),
team_a's P(win) series on each venue: Kalshi candlestick mid, Polymarket
prices-history last, both order-book (NO de-vig). Alignment is AS-OF (each
instant compares each venue's most recent actual observation; no lookahead,
no forward-fill of fabricated quotes — a gap is skipped).

Method (per regime; a map's series is split at kickoff into pre_match /
in_game slices, since leadership can flip):
- **Divergence:** |kalshi - poly| >= `lead_lag.divergence_threshold` (0.02),
  same sign, persisting >= `confirmation_snapshots` (3) consecutive compared
  instants (not a flicker). A cooldown of one convergence window prevents
  double-counting the same divergence.
- **Convergence / lead score** over `convergence_window_s` (300 s) after
  onset: with g0 = kalshi0 - poly0, dP = poly(t0+w) - poly0, dK =
  kalshi(t0+w) - kalshi0, **L = sign(g0) * (dP + dK)**. L > 0 ⇒ the follower
  (Polymarket) moved toward Kalshi ⇒ **Kalshi leads**; L < 0 ⇒ Polymarket
  leads. (Frozen sign convention: positive = Kalshi leads.)
- **Aggregate:** mean L per regime with a seeded event-block bootstrap by
  match (series). **PASS (a leader exists) iff the CI excludes 0**; leader =
  Kalshi if mean > 0 else Polymarket. A regime with < `min_divergences`
  (30) confirmed divergences is "insufficient sample" (bounded-date; never
  lower the bar).
- **Pre-move / co-move check (mandatory caveat):** if both venues reprice
  together within the confirmation interval (the gap closes symmetrically,
  L ≈ 0 with tight CI), there is NO tradeable lag however large the
  instantaneous gap looked — the report says so explicitly. A usable
  reference must be BOTH calibrated (G2) AND leading (G3); neither alone.

## [2026-07-22] RESULT — G3 lead-lag: Kalshi LEADS in-game, no lead pre-match

Artifact `G3_20260722T190614Z.json` (sha256 c0616f133690f202). 64 maps with
paired intraday series (the OE∩Polymarket-history overlap). Judged vs the
frozen G3 rule (leader iff bootstrap CI on mean lead L excludes 0; +L =
Kalshi leads).
- **pre_match:** n_divergences=264, mean L=+0.0025, CI [-0.0009, +0.0056]
  (52 event blocks) → **CI spans 0 → NO leader.** The venues co-move
  pre-match; no tradeable cross-venue lag.
- **in_game:** n_divergences=201, mean L=+0.0842, CI [+0.0558, +0.1132]
  (62 event blocks) → **CI excludes 0, positive → KALSHI LEADS.** When the
  venues diverge during a live map, Polymarket converges toward Kalshi.

Reading with G2: **in-game, Kalshi is BOTH calibrated (G2 pass, ECE 0.10 vs
Polymarket 0.20) AND leading (G3 pass) → a usable reference in that regime.**
Pre-match, Kalshi is calibrated but does NOT lead → calibration without lead
→ no tradeable cross-venue signal pre-match.

Caveats (carry into G4): sample is the ~1-month overlap (64 maps); series
are minute-grade candles/history, so measured lag is a LOWER bound on speed
edge; the 300s convergence window means the lead operates over minutes (its
tradeability depends on execution latency + the thin Polymarket depth from
G0, a separate measured capacity cost). Not yet corroborated by a live
recorder. STOP for human review before G4.

## [2026-07-22] FREEZE — G4 combination rule + bounded verdict date

The final go/no-go, assembled from the stored G0–G3 artifacts (every number
traces to one). Two layers, judged per regime:

- **Reference validity (the research question):** Kalshi is a USABLE
  REFERENCE in a regime iff ALL hold — G1 parity PASS (same claim, family)
  AND G2 calibration PASS for that regime (Kalshi ECE <= Poly ECE + margin)
  AND G3 lead PASS for that regime (Kalshi leads, CI excludes 0). Calibration
  without lead, or lead without calibration, is NOT a usable reference.
- **Tradeability (go/no-go for a *separate* future execution repo):**
  reference-valid in that regime AND the traded venue (Polymarket) clears the
  G0 depth floor AND the result is live-recorder-corroborated. (This repo is
  measurement-only; it never trades. A "reference valid but not yet
  tradeable" outcome is a SUCCESS, not a failure.)

Overall verdict:
- GO (execution justified) iff some regime is reference-valid AND tradeable.
- CONDITIONAL / NOT-YET iff some regime is reference-valid but tradeability
  is unmet (depth / live-corroboration / thin sample) → judged again at the
  bounded date after recorder accrual.
- KILL iff no regime is reference-valid.

**Bounded verdict date: 2026-09-30.** Whatever sample exists then is judged
by these frozen rules; short sample is remedied only by better measurement
or calendar accrual, never by moving a threshold.

## [2026-07-22] NOTE — Depth refinement: tier-1 only, top-of-book vs full book

The G0 live-depth number ($1–3/side) was measured over ALL open LoL markets
(mostly minor leagues) — a contaminated proxy. Re-measured on TIER-1 only
(is_tier1 on the event title; open Polymarket map-winner books, LCK regular
season live at run time; no playoff/international open to observe):
- **top-of-book /side: median $7.4, p75 $16, p90 $24, max $306** (n=62).
- **full book /side (Σ price×size all levels, thinner side): median $241.9,
  p75 $2,262, p90 $4,283, max $13,438.**

Reading: top-of-book is genuinely thin, but there is meaningful capacity
DEEPER in the book on tier-1 matches — a few hundred to a few thousand $ if
you walk levels (paying price impact). This ENRICHES but does NOT overturn
the frozen G0 depth gate, which is **top-of-book** median ≥ $250 and still
FAILS ($7.4). It does revise the tradeability read: capacity is modest-but-
real, not ~$0. Caveat: the DEEPEST matches (playoff/MSI/Worlds) were not
open at run time, so best-case depth is still unobserved — only the recorder
running through a finals series can measure it. Implication for the recorder
spec: capture FULL book + a price-impact curve, not just top-of-book.

## [2026-07-23] NOTE — Live recorder built + dress rehearsal (observe-only)

Built the recorder unit per the Recorder Field Guide (`ingest/record.py`,
`ops/`): polls both venues' OPEN tier-1 LoL map/series books, writes FULL-book
snapshots to `book_snapshots` (source='live', raw payload archived, top-of-book
+ cumulative $/side), restart-safe (cycle cursor committed atomically with its
rows; idempotent upsert), TRUE per-row fetch latency, memoized fixture catalog,
429/5xx/transport circuit breaker (per-market 404 = gap, NOT a trip), tier-1
filter, loud drop counts. Observe-only — no order/wallet/execution code.

Dress rehearsal (one live cycle, no keys): catalog 256 Polymarket + 140 Kalshi
open tier-1 fixtures; 357 snapshots written, 39 per-market gaps (404 tokens)
dropped-and-counted, zero false circuit-breaks. Latency ~65ms Kalshi / ~174ms
Polymarket. **Full-book depth on a complete live tier-1 snapshot (Polymarket,
min side, n=217): p50 $309, p90 $4,281, max $221k** — corroborates the deeper-
book capacity finding (top-of-book stays thin; the frozen G0 gate is top-of-
book and still fails).

Caveats pinned: Kalshi `orderbook_fp` full-book schema is UNVERIFIED (no live
LoL book was open at build time) — top-of-book comes from the verified market
fields; the raw orderbook is archived verbatim so the first live match pins the
parser. `RecorderConfig.kalshi_orderbook_verified=False` gates this. To convert
the CONDITIONAL G4 verdict, run the recorder continuously (weeks) through live
matches — ideally a playoff/MSI/Worlds series (deepest books, the one depth
regime unobserved) — then re-judge G2/G3 at the bounded date 2026-09-30.

## [2026-07-22] RESULT — G4 FINAL VERDICT: CONDITIONAL (reference valid in-game, not yet tradeable)

Artifact `G4_20260722T191945Z.json` (sha256 4b12f2ba15376ab9). Assembled
from the current G0–G3 artifacts (traced within). Refreshed inputs: G0
covered=91 (PASS), G1 253/253 (PASS), G2 pass both regimes (thin), G3
in-game Kalshi leads / pre-match no leader.

**VERDICT: CONDITIONAL — Kalshi is a usable price reference for tier-1 LoL
map-winner IN-GAME, but the edge is not yet tradeable.**
- **in_game: reference-valid = TRUE.** Parity PASS ∧ calibration PASS
  (Kalshi ECE 0.1016 vs Polymarket 0.2045) ∧ lead PASS (Kalshi leads,
  n_div=201, CI excludes 0). Kalshi is both the more-calibrated book and the
  one Polymarket converges toward.
- **pre_match: reference-valid = FALSE.** Calibrated (ECE 0.1158 vs 0.1604)
  but NO lead (CI spans 0) → truth arrives too late to trade → no signal.
- **tradeable = FALSE (both regimes).** Blockers: (1) Polymarket depth below
  the $250/side floor (live snapshot $1–2/side — a capacity kill risk);
  (2) no live-recorder corroboration (historical-only); (3) thin ~1-month
  sample (Polymarket price-history retention).

This is a SUCCESSFUL measurement outcome, not a failure: a real, regime-
specific reference-validity result plus a clear, honest reason it is not yet
a trade. Per the frozen rule it is **judged again at the bounded date
2026-09-30** after recorder accrual (which alone can harden the sample,
supply the honest tier-1 depth number, and live-corroborate the in-game
lead). No threshold may move in the interim. All gates G0–G4 complete.
