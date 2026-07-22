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
