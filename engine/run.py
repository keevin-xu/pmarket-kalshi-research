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
    raise NotImplementedError(f"gate={args.gate!r} not wired yet (G0 is)")


if __name__ == "__main__":
    raise SystemExit(main())
