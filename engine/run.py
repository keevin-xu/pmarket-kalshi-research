"""Orchestration + CLI. Runs the chain and STOPS at each gate for human
review — momentum must not carry a failed gate. SPORT-AGNOSTIC: every gate
takes a `sport` and reads `sport.params`; no gate names a sport.

Chain: G0 census -> G1 parity -> G2 calibration -> G3 lead-lag -> G4 verdict.
Each stage reads point-in-time via core.db.store only, writes a stored
artifact under the sport's own data/<sport>/artifacts, and records discards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from core.config import CONFIG
from core.census import coverage as cov
from core.census import depth as depthmod
from core.census import sweep
from core.parity import settlement
from core.db import store
from sports import get_sport

GATES = ["G0", "G1", "G2", "G3", "G4"]


def _write_artifact(conn, sport, gate: str, payload: dict) -> str:
    out = Path(sport.params.artifacts_dir)
    out.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    blob = json.dumps(payload, indent=2, sort_keys=True, default=str)
    h = hashlib.sha256(blob.encode()).hexdigest()
    path = out / f"{gate}_{run_id}.json"
    path.write_text(blob)
    conn.execute(
        "INSERT OR REPLACE INTO run_artifacts (run_id, gate, created_ts, path, table_hash) "
        "VALUES (?,?,?,?,?)",
        [run_id, gate, store.to_ts(datetime.now(timezone.utc)), str(path), h],
    )
    conn.commit()
    return str(path)


def run_g0(conn, sport, oe_paths: list[str], *, live_depth: bool = True) -> dict:
    """G0 feasibility census, judged vs the sport's FROZEN census params."""
    store.init_schema(conn)
    cp = sport.params.census

    oe = sport.load_matches(oe_paths)                       # neutral spine
    store.upsert_matches(conn, oe)

    from core.ingest.polymarket import PolymarketAdapter
    pm = PolymarketAdapter()
    k_recs, k_contracts = sweep.sweep_kalshi(sport)
    p_recs, p_contracts = sweep.sweep_polymarket(sport, pm)
    store.upsert_contracts(conn, k_contracts)
    store.upsert_contracts(conn, p_contracts)

    pm_oldest = min((r["ts"] for r in p_recs), default=None)
    pm_truncated = bool(getattr(pm, "pagination_capped", False)
                        and pm_oldest and pm_oldest > cp.window_start)

    coverage = cov.coverage_report(
        oe, {CONFIG.venues.KALSHI: k_recs, CONFIG.venues.POLYMARKET: p_recs}, cp)

    depth = {}
    if live_depth:
        try:
            sweep.sweep_polymarket_live_depth(sport, conn)
        except Exception as e:                              # a hiccup must not fake a number
            depth["_error"] = f"live depth sweep failed: {e!r}"
    for regime in (CONFIG.regimes.PRE_MATCH, CONFIG.regimes.IN_GAME):
        depth[regime] = depthmod.depth_at_signal_moments(conn, CONFIG.venues.POLYMARKET, regime)

    min_depth = cp.min_depth_usd_per_side
    pm_depth_vals = [d.get("median_usd") for d in depth.values()
                     if isinstance(d, dict) and d.get("median_usd") is not None]
    depth_pass = any(v >= min_depth for v in pm_depth_vals) if pm_depth_vals else None

    per_family_verdict = {}
    for fam in cp.families_phase1:
        both_exist = all(fam in coverage["existence"].get(v, [])
                         for v in (CONFIG.venues.KALSHI, CONFIG.venues.POLYMARKET))
        per_family_verdict[fam] = {
            "both_venues_exist": both_exist,
            "n_covered": coverage["per_family_covered"].get(fam, 0),
            "go_to_G1": bool(both_exist and coverage["per_family_covered"].get(fam, 0)
                             >= cp.min_covered_matches),
        }

    payload = {
        "gate": "G0", "sport": sport.key,
        "frozen_rules": {
            "min_covered_matches": cp.min_covered_matches,
            "min_depth_usd_per_side": min_depth,
            "window_start": cp.window_start,
            "tier1_leagues": list(cp.tier1_leagues),
            "families_phase1": list(cp.families_phase1),
        },
        "coverage": coverage,
        "polymarket_sweep": {
            "n_records": len(p_recs), "oldest_ts": pm_oldest,
            "pagination_capped": bool(getattr(pm, "pagination_capped", False)),
            "in_window_truncated": pm_truncated,
        },
        "depth_live_polymarket": depth,
        "depth_gate_pass": depth_pass,
        "per_family_verdict": per_family_verdict,
        "caveats": [
            "coverage counts SERIES ((team-pair, day)); neutral gameid is per-map.",
            "depth is LIVE-only (settled books are gone at resolution); a one-shot "
            "snapshot, top-of-book only — the binding number needs the recorder.",
            "cross-venue window is bounded by Kalshi's launch for this sport; low "
            "coverage %% reflects short venue history, not poor matching.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, sport, "G0", payload)
    return payload


def run_g1(conn, sport, oe_paths: list[str]) -> dict:
    """G1 settlement parity for map_winner, judged vs the sport's parity params."""
    store.init_schema(conn)
    oe_maps = sport.load_map_results(oe_paths)
    k = sweep.sweep_kalshi_map_results(sport)
    p = sweep.sweep_polymarket_map_results(sport)
    res = settlement.check_family_parity(oe_maps, k, p, sport.params.parity, family="map_winner")

    for d in res.disagreements:
        store.record_discard(conn, "parity", d.get("kind", "parity_mismatch"), match_id=None)

    payload = {
        "gate": "G1", "sport": sport.key, "family": res.family,
        "frozen_rules": {"min_family_pass_rate": sport.params.parity.min_family_pass_rate,
                         "min_aligned_maps": sport.params.parity.min_aligned_maps},
        "n_aligned_played_maps": res.n_aligned, "n_agree": res.n_agree,
        "agreement_rate": res.pass_rate,
        "oe_agreement": {"kalshi": res.oe_agree_kalshi, "polymarket": res.oe_agree_polymarket},
        "n_void_breaks": res.n_void_breaks, "verdict": res.verdict,
        "passed_gate": res.passed_gate, "disagreements_sample": res.disagreements[:25],
        "caveats": [
            "neutral source is the arbiter; venue-vs-venue agreement is the gate.",
            "resolution source differs (Kalshi governing-league vs Polymarket "
            "UMA/gol.gg) — documented caveat, not a fail, given agreement + voids hold.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, sport, "G1", payload)
    return payload


def run_g2(conn, sport, oe_paths: list[str]) -> dict:
    """G2 calibration per regime, judged vs the sport's reference params."""
    store.init_schema(conn)
    from core.reference import calib_data, calibration
    points = calib_data.build_points(sport, oe_paths)
    min_n = 50
    regimes = {}
    for regime in (CONFIG.regimes.PRE_MATCH, CONFIG.regimes.IN_GAME):
        cmp = calibration.compare_venues(points, regime, sport.params.reference)
        k_n, p_n = cmp["kalshi"].get("n", 0), cmp["polymarket"].get("n", 0)
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
        "gate": "G2", "sport": sport.key, "family": "map_winner",
        "frozen_rules": {"metric": "ECE",
                         "pass_margin": sport.params.reference.calibration_pass_margin,
                         "min_sample": min_n,
                         "in_game_checkpoint_s": sport.params.reference.in_game_checkpoint_s},
        "n_points_total": len(points), "regimes": regimes,
        "caveats": [
            "one point per map (Blue-side team); outcome from the neutral source.",
            "pre_match = mid at kickoff; in_game = mid at kickoff+checkpoint_s.",
            "prices are order-book mid (Kalshi) / last (Polymarket), NO de-vig.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, sport, "G2", payload)
    return payload


def run_g3(conn, sport, oe_paths: list[str]) -> dict:
    """G3 lead-lag per regime, judged vs the sport's lead_lag params."""
    store.init_schema(conn)
    from core.reference import lead_lag, leadlag_data
    llp = sport.params.lead_lag
    maps = leadlag_data.build_map_series(sport, oe_paths)
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
            for d in lead_lag.detect_divergences(ps, ks, regime, match_id=mp["match_id"],
                                                 ll_params=llp):
                convs.append(lead_lag.convergence_after(d, mp["poly"], mp["kalshi"], ll_params=llp))
                divs.append(d)
                mids.append(mp["match_id"])
        rep = lead_lag.lead_lag_report(divs, convs, mids, regime)
        n = rep["n_divergences"]
        if n < llp.min_divergences:
            rep["verdict"] = f"insufficient sample (n_divergences={n} < {llp.min_divergences})"
            rep["passed"] = None
        elif rep["leader"]:
            rep["verdict"] = f"LEADS: {rep['leader']} (CI excludes 0)"
            rep["passed"] = True
        else:
            rep["verdict"] = "no leader — CI spans 0; no tradeable cross-venue lag"
            rep["passed"] = False
        regimes[regime] = rep

    payload = {
        "gate": "G3", "sport": sport.key, "family": "map_winner",
        "frozen_rules": {"divergence_threshold": llp.divergence_threshold,
                         "confirmation_snapshots": llp.confirmation_snapshots,
                         "convergence_window_s": llp.convergence_window_s,
                         "ci_level": CONFIG.bootstrap.ci_level,
                         "min_divergences": llp.min_divergences,
                         "sign": "positive lead score = Kalshi leads"},
        "n_maps": len(maps), "regimes": regimes,
        "caveats": [
            "series are Kalshi candle mid vs Polymarket prices-history last, "
            "as-of aligned (no lookahead / no fabricated fills).",
            "if both venues reprice together the lead score ~0 with tight CI -> "
            "no tradeable lag despite a large instantaneous gap.",
            "sample is the ~1-month history overlap; thin/CI-spanning results are "
            "resolved by recorder accrual, not a bar move.",
        ],
    }
    payload["artifact_path"] = _write_artifact(conn, sport, "G3", payload)
    return payload


def _latest_artifact(sport, gate: str) -> dict | None:
    files = sorted(Path(sport.params.artifacts_dir).glob(f"{gate}_*.json"),
                   key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    d = json.loads(files[-1].read_text())
    d["artifact_path"] = str(files[-1])
    return d


def run_g4(conn, sport) -> dict:
    """G4 final verdict — combine the sport's latest stored G0–G3 artifacts."""
    store.init_schema(conn)
    from core.analysis import report
    g = {name: _latest_artifact(sport, gate)
         for name, gate in (("census", "G0"), ("parity", "G1"),
                            ("calibration", "G2"), ("lead_lag", "G3"))}
    missing = [k for k, v in g.items() if v is None]
    if missing:
        raise SystemExit(f"G4 needs prior artifacts; missing: {missing}. Run those gates first.")
    verdict = report.build_verdict(
        g["census"], g["parity"], g["calibration"], g["lead_lag"],
        min_depth_usd=sport.params.census.min_depth_usd_per_side,
        bounded_date=sport.params.bounded_verdict_date, live_corroborated=False)
    verdict["sport"] = sport.key
    verdict["artifact_path"] = _write_artifact(conn, sport, "G4", verdict)
    return verdict


# --- printers (read the payload dict; sport-agnostic) ------------------------
def _fmt(x):
    return "n/a" if x is None else f"{x:.4f}"


def _print_g0(p: dict) -> None:
    c = p["coverage"]
    print("\n===== G0 FEASIBILITY CENSUS — verdict vs frozen rules =====")
    print(f"tier-1 series in window: {c['n_tier1_series']}")
    print(f"covered by BOTH venues:  {c['n_covered']}  (gate >= {c['gate_min_covered']}) -> "
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
    print("STOP — G0 is a human-review gate.")


def _print_g1(p: dict) -> None:
    print("\n===== G1 SETTLEMENT PARITY (map_winner) — verdict vs frozen rule =====")
    print(f"aligned played maps: {p['n_aligned_played_maps']} (min {p['frozen_rules']['min_aligned_maps']})")
    print(f"venue agreement:     {p['n_agree']}/{p['n_aligned_played_maps']} "
          f"= {p['agreement_rate']*100:.1f}%  (gate >= {p['frozen_rules']['min_family_pass_rate']*100:.0f}%)")
    print(f"OE corroboration:    kalshi={p['oe_agreement']['kalshi']} polymarket={p['oe_agreement']['polymarket']}")
    print(f"void-handling breaks: {p['n_void_breaks']}")
    print(f"VERDICT: {p['verdict']}  -> passed_gate={p['passed_gate']}")
    print(f"\nartifact: {p['artifact_path']}")
    print("STOP — G1 is a human-review gate.")


def _print_g2(p: dict) -> None:
    print("\n===== G2 CALIBRATION (map_winner) — verdict vs frozen rule =====")
    print(f"total calibration points: {p['n_points_total']}  "
          f"(metric=ECE, pass = Kalshi ECE <= Poly ECE + {p['frozen_rules']['pass_margin']})")
    for regime, cmp in p["regimes"].items():
        k, pm = cmp["kalshi"], cmp["polymarket"]
        print(f"\n[{regime}]")
        print(f"  kalshi:     n={k.get('n')}  ECE={_fmt(k.get('ece'))}  Brier={_fmt(k.get('brier'))}")
        print(f"  polymarket: n={pm.get('n')}  ECE={_fmt(pm.get('ece'))}  Brier={_fmt(pm.get('brier'))}")
        print(f"  VERDICT: {cmp['verdict']}  -> passed={cmp['passed']}")
    print(f"\nartifact: {p['artifact_path']}")
    print("STOP — G2 is a human-review gate.")


def _print_g3(p: dict) -> None:
    print("\n===== G3 LEAD-LAG (map_winner) — verdict vs frozen rule =====")
    print(f"maps with paired intraday series: {p['n_maps']}  (+lead => Kalshi leads)")
    for regime, rep in p["regimes"].items():
        b = rep["signed_convergence"]
        print(f"\n[{regime}]  n_divergences={rep['n_divergences']}")
        print(f"  mean lead L={_fmt(b.get('point'))}  CI[{_fmt(b.get('ci_lo'))}, {_fmt(b.get('ci_hi'))}]  "
              f"n_blocks={b.get('n_blocks')}")
        print(f"  VERDICT: {rep['verdict']}  -> passed={rep['passed']}")
    print(f"\nartifact: {p['artifact_path']}")
    print("STOP — G3 is a human-review gate.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pmarket-kalshi-research pipeline")
    ap.add_argument("--sport", default="lol", help="sport key (see sports/REGISTRY)")
    ap.add_argument("--gate", choices=GATES, required=True, help="run a single gate")
    ap.add_argument("--db", default=None, help="override the sport's db path")
    ap.add_argument("--oe-glob", default=None, help="override neutral-source files")
    ap.add_argument("--no-live-depth", action="store_true", help="skip the live depth sweep")
    args = ap.parse_args(argv)

    sport = get_sport(args.sport)
    conn = store.connect(args.db or sport.params.db_path)

    if args.gate == "G4":
        from core.analysis import report
        v = run_g4(conn, sport)
        print(report.render(v))
        print(f"\nartifact: {v['artifact_path']}")
        print("G4 is the terminal gate — verdict stands until the bounded date.")
        return 0

    paths = sorted(__import__("glob").glob(args.oe_glob)) if args.oe_glob else sport.outcome_paths()
    if not paths:
        print("no neutral-source files found; pass --oe-glob or populate the sport's raw dir")
        return 2
    if args.gate == "G0":
        _print_g0(run_g0(conn, sport, paths, live_depth=not args.no_live_depth))
    elif args.gate == "G1":
        _print_g1(run_g1(conn, sport, paths))
    elif args.gate == "G2":
        _print_g2(run_g2(conn, sport, paths))
    elif args.gate == "G3":
        _print_g3(run_g3(conn, sport, paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
