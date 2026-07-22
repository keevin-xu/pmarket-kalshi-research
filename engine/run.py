"""Orchestration + CLI. Runs the chain and STOPS at each gate for human
review — momentum must not carry a failed gate.

Chain: ingest -> G0 census -> G1 parity -> G2 calibration -> G3 lead-lag
-> G4 verdict. Each stage reads point-in-time via db.store only, writes a
stored artifact, and records discards with reason codes.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from config import CONFIG
from census import coverage as cov
from census import depth as depthmod
from census import sweep
from parity import settlement
from db import store

GATES = ["ingest", "G0", "G1", "G2", "G3", "G4"]


def _write_artifact(conn, gate: str, payload: dict) -> str:
    from config import ARTIFACTS_DIR
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    blob = json.dumps(payload, indent=2, sort_keys=True, default=str)
    h = hashlib.sha256(blob.encode()).hexdigest()
    path = ARTIFACTS_DIR / f"{gate}_{run_id}.json"
    path.write_text(blob)
    conn.execute(
        "INSERT OR REPLACE INTO run_artifacts (run_id, gate, created_ts, path, table_hash) "
        "VALUES (?,?,?,?,?)",
        [run_id, gate, store.to_ts(datetime.now(timezone.utc)), str(path), h],
    )
    conn.commit()
    return str(path)


def run_g0(conn, oe_paths: list[str], *, live_depth: bool = True) -> dict:
    """G0 feasibility census. Judged against the FROZEN thresholds in
    DECISIONS.md [2026-07-12]. Returns the artifact payload."""
    store.init_schema(conn)

    # 1) neutral spine
    oe = sweep.load_oe_matches(oe_paths)
    store.upsert_matches(conn, oe)

    # 2) venue phase-1 contracts (settled/closed) -> coverage records
    from ingest.polymarket import PolymarketAdapter
    pm = PolymarketAdapter()
    k_recs, k_contracts = sweep.sweep_kalshi()
    p_recs, p_contracts = sweep.sweep_polymarket(pm)
    store.upsert_contracts(conn, k_contracts)
    store.upsert_contracts(conn, p_contracts)

    # honesty: if Gamma's offset cap truncated BEFORE the window start, we may
    # be missing in-window matches — surface it, never hide it.
    pm_oldest = min((r["ts"] for r in p_recs), default=None)
    pm_truncated = bool(getattr(pm, "pagination_capped", False)
                        and pm_oldest and pm_oldest > CONFIG.census.window_start)

    # 3) coverage vs neutral schedule
    coverage = cov.coverage_report(
        oe, {CONFIG.venues.KALSHI: k_recs, CONFIG.venues.POLYMARKET: p_recs})

    # 4) depth — LIVE Polymarket books (settled-book depth is a bug)
    depth = {}
    if live_depth:
        try:
            sweep.sweep_polymarket_live_depth(conn)
        except Exception as e:  # network hiccup must not fake a number
            depth["_error"] = f"live depth sweep failed: {e!r}"
    for regime in (CONFIG.regimes.PRE_MATCH, CONFIG.regimes.IN_GAME):
        depth[regime] = depthmod.depth_at_signal_moments(
            conn, CONFIG.venues.POLYMARKET, regime)

    # 5) verdict vs frozen thresholds, per family (series_winner may fail on
    #    Kalshi per recon; map_winner is the live cross-venue family).
    min_depth = CONFIG.census.min_depth_usd_per_side
    pm_depth_vals = [d.get("median_usd") for r, d in depth.items()
                     if isinstance(d, dict) and d.get("median_usd") is not None]
    depth_pass = any(v >= min_depth for v in pm_depth_vals) if pm_depth_vals else None

    per_family_verdict = {}
    for fam in CONFIG.census.families_phase1:
        both_exist = all(fam in coverage["existence"].get(v, [])
                         for v in (CONFIG.venues.KALSHI, CONFIG.venues.POLYMARKET))
        per_family_verdict[fam] = {
            "both_venues_exist": both_exist,
            "n_covered": coverage["per_family_covered"].get(fam, 0),
            "go_to_G1": bool(both_exist
                             and coverage["per_family_covered"].get(fam, 0)
                             >= CONFIG.census.min_covered_matches),
        }

    payload = {
        "gate": "G0",
        "frozen_rules": {
            "min_covered_matches": CONFIG.census.min_covered_matches,
            "min_depth_usd_per_side": min_depth,
            "window_start": CONFIG.census.window_start,
            "tier1_oe_leagues": list(CONFIG.census.tier1_oe_leagues),
            "families_phase1": list(CONFIG.census.families_phase1),
        },
        "coverage": coverage,
        "polymarket_sweep": {
            "n_records": len(p_recs),
            "oldest_ts": pm_oldest,
            "pagination_capped": bool(getattr(pm, "pagination_capped", False)),
            "in_window_truncated": pm_truncated,
        },
        "depth_live_polymarket": depth,
        "depth_gate_pass": depth_pass,
        "per_family_verdict": per_family_verdict,
        "caveats": [
            "coverage counts SERIES ((team-pair, day)); OE gameid is per-map.",
            "depth is LIVE-only (settled books are gone at resolution). This is a "
            "ONE-SHOT snapshot of whatever LoL markets are open at run time — mixed "
            "tier (often minor leagues), top-of-book only, NOT a rigorous tier-1 "
            "signal-moment measurement. Indicative, not the final depth verdict; the "
            "binding number needs the live recorder over tier-1 matches at kickoff.",
            "series_winner on Kalshi (KXLOL) had 0 settled match markets; Kalshi's "
            "match-level LoL product is the MAP winner. series_winner is excluded.",
            "cross-venue window is bounded by Kalshi's LoL launch (~2026-05-06); "
            "low coverage %% reflects that short venue history, not poor matching. "
            "Team-name join misses non-derivable aliases (e.g. BLG) -> UNDERcount.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, "G0", payload)
    return payload


def run_g1(conn, oe_paths: list[str]) -> dict:
    """G1 settlement parity for map_winner. Judged vs the FROZEN rule in
    DECISIONS.md [2026-07-13]. OE is the neutral arbiter."""
    store.init_schema(conn)
    oe_maps = sweep.load_oe_map_results(oe_paths)
    k = sweep.sweep_kalshi_map_results()
    p = sweep.sweep_polymarket_map_results()
    res = settlement.check_family_parity(oe_maps, k, p, family="map_winner")

    # every disagreement is a stored discard with a reason
    for d in res.disagreements:
        store.record_discard(conn, "parity", d.get("kind", "parity_mismatch"),
                             match_id=None)

    payload = {
        "gate": "G1", "family": res.family,
        "frozen_rules": {"min_family_pass_rate": CONFIG.parity.min_family_pass_rate,
                         "min_aligned_maps": CONFIG.parity.min_aligned_maps},
        "n_aligned_played_maps": res.n_aligned,
        "n_agree": res.n_agree,
        "agreement_rate": res.pass_rate,
        "oe_agreement": {"kalshi": res.oe_agree_kalshi,
                         "polymarket": res.oe_agree_polymarket},
        "n_void_breaks": res.n_void_breaks,
        "verdict": res.verdict,
        "passed_gate": res.passed_gate,
        "disagreements_sample": res.disagreements[:25],
        "caveats": [
            "OE is the neutral arbiter; venue-vs-venue agreement is the gate, "
            "venue-vs-OE is corroboration.",
            "resolution source differs (Kalshi governing-league result vs "
            "Polymarket UMA/gol.gg) — a documented caveat, not a fail, given "
            "result agreement + void consistency hold.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, "G1", payload)
    return payload


def run_g2(conn, oe_paths: list[str]) -> dict:
    """G2 calibration for map_winner, per regime. Judged vs the FROZEN rule
    in DECISIONS.md [2026-07-13]: Kalshi ECE <= Polymarket ECE + margin."""
    store.init_schema(conn)
    from reference import calib_data, calibration
    points = calib_data.build_points(oe_paths)
    min_n = 50
    regimes = {}
    for regime in (CONFIG.regimes.PRE_MATCH, CONFIG.regimes.IN_GAME):
        cmp = calibration.compare_venues(points, regime)
        k_n = cmp["kalshi"].get("n", 0)
        p_n = cmp["polymarket"].get("n", 0)
        if k_n < min_n or p_n < min_n:
            cmp["verdict"] = f"insufficient sample (kalshi n={k_n}, poly n={p_n}, min {min_n})"
            cmp["passed"] = None
        else:
            cmp["verdict"] = ("PASS — Kalshi at least as calibrated"
                              if cmp["kalshi_calibrated_vs_poly"]
                              else "FAIL — Kalshi less calibrated than Polymarket")
            cmp["passed"] = cmp["kalshi_calibrated_vs_poly"]
        regimes[regime] = cmp

    payload = {
        "gate": "G2", "family": "map_winner",
        "frozen_rules": {"metric": "ECE", "pass_margin": CONFIG.reference.calibration_pass_margin,
                         "min_sample": min_n, "buckets": list(CONFIG.reference.calibration_buckets)},
        "n_points_total": len(points),
        "regimes": regimes,
        "caveats": [
            "one point per map (Blue-side team); price = P(team_a wins), "
            "outcome from Oracle's Elixir (neutral).",
            "pre_match = mid at kickoff; in_game = mid at kickoff+600s (10-min "
            "game clock), maps with gamelen>=600s only.",
            "prices are order-book mid (Kalshi) / last (Polymarket), NO de-vig.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, "G2", payload)
    return payload


def _latest_artifact(gate: str) -> dict | None:
    from config import ARTIFACTS_DIR
    files = sorted(ARTIFACTS_DIR.glob(f"{gate}_*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    d = json.loads(files[-1].read_text())
    d["artifact_path"] = str(files[-1])   # the on-disk JSON doesn't store its own path
    return d


def run_g4(conn) -> dict:
    """G4 final verdict — combine the latest stored G0–G3 artifacts per the
    frozen G4 rule. No re-measuring; every number traces to its artifact."""
    store.init_schema(conn)
    from analysis import report
    g = {name: _latest_artifact(gate)
         for name, gate in (("census", "G0"), ("parity", "G1"),
                            ("calibration", "G2"), ("lead_lag", "G3"))}
    missing = [k for k, v in g.items() if v is None]
    if missing:
        raise SystemExit(f"G4 needs prior artifacts; missing: {missing}. Run those gates first.")
    verdict = report.build_verdict(g["census"], g["parity"], g["calibration"],
                                   g["lead_lag"], live_corroborated=False)
    verdict["artifact_path"] = _write_artifact(conn, "G4", verdict)
    return verdict


def run_g3(conn, oe_paths: list[str]) -> dict:
    """G3 lead-lag for map_winner, per regime. Judged vs the FROZEN rule in
    DECISIONS.md [2026-07-22]: a leader exists iff the bootstrap CI on the
    signed lead score excludes zero."""
    store.init_schema(conn)
    from reference import lead_lag, leadlag_data
    maps = leadlag_data.build_map_series(oe_paths)
    preroll = leadlag_data._PREROLL_S

    regimes = {}
    for regime in (CONFIG.regimes.PRE_MATCH, CONFIG.regimes.IN_GAME):
        divs, convs, mids = [], [], []
        for mp in maps:
            lo, hi = ((mp["kickoff"] - preroll, mp["kickoff"])
                      if regime == CONFIG.regimes.PRE_MATCH
                      else (mp["kickoff"], mp["map_end"] + 1))
            ps = leadlag_data.slice_series(mp["poly"], lo, hi)
            ks = leadlag_data.slice_series(mp["kalshi"], lo, hi)
            if len(ps) < 2 or len(ks) < 2:
                continue
            for d in lead_lag.detect_divergences(ps, ks, regime, match_id=mp["match_id"]):
                # measure convergence on the FULL series (window may cross regime edge)
                convs.append(lead_lag.convergence_after(d, mp["poly"], mp["kalshi"]))
                divs.append(d)
                mids.append(mp["match_id"])
        rep = lead_lag.lead_lag_report(divs, convs, mids, regime)
        n = rep["n_divergences"]
        if n < CONFIG.lead_lag.min_divergences:
            rep["verdict"] = f"insufficient sample (n_divergences={n} < {CONFIG.lead_lag.min_divergences})"
            rep["passed"] = None
        elif rep["leader"]:
            rep["verdict"] = f"LEADS: {rep['leader']} (CI excludes 0)"
            rep["passed"] = True
        else:
            rep["verdict"] = "no leader — CI spans 0; no tradeable cross-venue lag"
            rep["passed"] = False
        regimes[regime] = rep

    payload = {
        "gate": "G3", "family": "map_winner",
        "frozen_rules": {"divergence_threshold": CONFIG.lead_lag.divergence_threshold,
                         "confirmation_snapshots": CONFIG.lead_lag.confirmation_snapshots,
                         "convergence_window_s": CONFIG.lead_lag.convergence_window_s,
                         "ci_level": CONFIG.lead_lag.ci_level,
                         "min_divergences": CONFIG.lead_lag.min_divergences,
                         "sign": "positive lead score = Kalshi leads"},
        "n_maps": len(maps),
        "regimes": regimes,
        "caveats": [
            "series are Kalshi candle mid vs Polymarket prices-history last, "
            "as-of aligned (no lookahead / no fabricated fills).",
            "if both venues reprice together within the confirmation interval "
            "the lead score ~0 with tight CI -> NO tradeable lag despite a "
            "large instantaneous gap.",
            "sample is the ~1-month OE-and-Polymarket-history overlap; a thin "
            "or CI-spanning result is resolved by recorder accrual, not a bar move.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, "G3", payload)
    return payload


def _print_g3(p: dict) -> None:
    print("\n===== G3 LEAD-LAG (map_winner) — verdict vs frozen rule =====")
    print(f"maps with paired intraday series: {p['n_maps']}  "
          f"(+lead => Kalshi leads; leader iff bootstrap CI excludes 0)")
    for regime, rep in p["regimes"].items():
        b = rep["signed_convergence"]
        print(f"\n[{regime}]  n_divergences={rep['n_divergences']}")
        print(f"  mean lead L={_fmt(b.get('point'))}  "
              f"CI[{_fmt(b.get('ci_lo'))}, {_fmt(b.get('ci_hi'))}]  "
              f"n_blocks={b.get('n_blocks')}")
        print(f"  VERDICT: {rep['verdict']}  -> passed={rep['passed']}")
    print(f"\nartifact: {p['artifact_path']}")
    print("STOP — G3 is a human-review gate. Do not proceed to G4 without review.")


def _print_g2(p: dict) -> None:
    print("\n===== G2 CALIBRATION (map_winner) — verdict vs frozen rule =====")
    print(f"total calibration points: {p['n_points_total']}  "
          f"(metric=ECE, pass = Kalshi ECE <= Poly ECE + {p['frozen_rules']['pass_margin']})")
    for regime, cmp in p["regimes"].items():
        k, pm = cmp["kalshi"], cmp["polymarket"]
        print(f"\n[{regime}]")
        print(f"  kalshi:     n={k.get('n')}  ECE={_fmt(k.get('ece'))}  "
              f"Brier={_fmt(k.get('brier'))}  logloss={_fmt(k.get('log_loss'))}")
        print(f"  polymarket: n={pm.get('n')}  ECE={_fmt(pm.get('ece'))}  "
              f"Brier={_fmt(pm.get('brier'))}  logloss={_fmt(pm.get('log_loss'))}")
        print(f"  VERDICT: {cmp['verdict']}  -> passed={cmp['passed']}")
    print(f"\nartifact: {p['artifact_path']}")
    print("STOP — G2 is a human-review gate. Do not proceed to G3 without review.")


def _fmt(x):
    return "n/a" if x is None else f"{x:.4f}"


def _print_g1(p: dict) -> None:
    print("\n===== G1 SETTLEMENT PARITY (map_winner) — verdict vs frozen rule =====")
    print(f"aligned played maps: {p['n_aligned_played_maps']} "
          f"(min {p['frozen_rules']['min_aligned_maps']})")
    print(f"venue agreement:     {p['n_agree']}/{p['n_aligned_played_maps']} "
          f"= {p['agreement_rate']*100:.1f}%  (gate >= "
          f"{p['frozen_rules']['min_family_pass_rate']*100:.0f}%)")
    print(f"OE corroboration:    kalshi={p['oe_agreement']['kalshi']} "
          f"polymarket={p['oe_agreement']['polymarket']}")
    print(f"void-handling breaks: {p['n_void_breaks']}")
    print(f"VERDICT: {p['verdict']}  -> passed_gate={p['passed_gate']}")
    if p["disagreements_sample"]:
        print("sample disagreements:")
        for d in p["disagreements_sample"][:8]:
            print("  ", d)
    print(f"\nartifact: {p['artifact_path']}")
    print("STOP — G1 is a human-review gate. Do not proceed to G2 without review.")


def _print_g0(p: dict) -> None:
    c = p["coverage"]
    print("\n===== G0 FEASIBILITY CENSUS — verdict vs frozen rules =====")
    print(f"tier-1 series in window: {c['n_tier1_series']}")
    print(f"covered by BOTH venues:  {c['n_covered']}  "
          f"(gate >= {c['gate_min_covered']}) -> "
          f"{'PASS' if c['passes_coverage'] else 'FAIL'}")
    print(f"coverage % (diagnostic): {c['coverage_pct']*100:.1f}%")
    print(f"existence: {c['existence']}")
    print("per-family:")
    for fam, v in p["per_family_verdict"].items():
        print(f"  {fam:14} both_exist={v['both_venues_exist']!s:5} "
              f"n_covered={v['n_covered']:4}  go_to_G1={v['go_to_G1']}")
    if p["depth_live_polymarket"].get("_error"):
        print(f"depth WARNING: {p['depth_live_polymarket']['_error']}")
    print(f"depth gate (live PM >= ${p['frozen_rules']['min_depth_usd_per_side']}/side): "
          f"{p['depth_gate_pass']}")
    for reg in (CONFIG.regimes.PRE_MATCH, CONFIG.regimes.IN_GAME):
        d = p["depth_live_polymarket"].get(reg, {})
        print(f"  {reg}: n={d.get('n')} median_usd={d.get('median_usd')}")
    print(f"\nartifact: {p['artifact_path']}")
    print("STOP — G0 is a human-review gate. Do not proceed to G1 without review.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pmarket-kalshi-research pipeline")
    ap.add_argument("--gate", choices=GATES, help="run a single gate")
    ap.add_argument("--db", default=None, help="override db path")
    ap.add_argument("--oe-glob", default="data/raw/oracleselixer/202[56].csv",
                    help="Oracle's Elixir CSVs (neutral spine) for G0")
    ap.add_argument("--no-live-depth", action="store_true",
                    help="skip the live Polymarket depth sweep")
    args = ap.parse_args(argv)

    conn = store.connect(args.db)
    if args.gate == "G0":
        paths = sorted(glob.glob(args.oe_glob))
        if not paths:
            print(f"no OE CSVs matched {args.oe_glob!r}")
            return 2
        p = run_g0(conn, paths, live_depth=not args.no_live_depth)
        _print_g0(p)
        return 0
    if args.gate in ("G1", "G2", "G3"):
        paths = sorted(glob.glob(args.oe_glob))
        if not paths:
            print(f"no OE CSVs matched {args.oe_glob!r}")
            return 2
        {"G1": lambda: _print_g1(run_g1(conn, paths)),
         "G2": lambda: _print_g2(run_g2(conn, paths)),
         "G3": lambda: _print_g3(run_g3(conn, paths))}[args.gate]()
        return 0
    if args.gate == "G4":
        from analysis import report
        v = run_g4(conn)
        print(report.render(v))
        print(f"\nartifact: {v['artifact_path']}")
        print("G4 is the terminal gate — verdict stands until the bounded date "
              "or a recorder-accrued re-run.")
        return 0
    raise NotImplementedError(f"gate={args.gate!r} not wired yet (G0..G4 are)")


if __name__ == "__main__":
    raise SystemExit(main())
