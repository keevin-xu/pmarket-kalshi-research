"""GATE G4 — verdict-first reporting.

Lead with the verdict against the PRE-REGISTERED rule, then the number, its
n, its CI, and caveat flags (hist-bounds, censoring, fallbacks used,
reference-validation status, settlement-parity coverage). An instrument
failure/outage is not a market result and is reported explicitly. Every
number must trace to a stored artifact under data/artifacts/ (run_artifacts).
"""
from __future__ import annotations

from core.config import CONFIG

REGIMES = (CONFIG.regimes.PRE_MATCH, CONFIG.regimes.IN_GAME)


def build_verdict(census: dict, parity: dict, calibration: dict, lead_lag: dict,
                  *, min_depth_usd: float, bounded_date: str,
                  live_corroborated: bool = False) -> dict:
    """Combine the stored gate artifacts into a go/no-go per the frozen G4
    rule. Reference-valid[regime] = parity ∧ calib[regime] ∧ lead[regime];
    tradeable[regime] = reference-valid ∧ depth ∧ live-corroboration. Short
    sample defers to the bounded date, never an early kill/pass."""
    parity_pass = bool(parity.get("passed_gate"))
    depth_pass = bool(census.get("depth_gate_pass"))
    calib = calibration.get("regimes", {})
    lead = lead_lag.get("regimes", {})

    regimes = {}
    any_ref_valid = any_tradeable = False
    for r in REGIMES:
        calib_pass = calib.get(r, {}).get("passed")
        lead_pass = lead.get(r, {}).get("passed")
        ref_valid = bool(parity_pass and calib_pass and lead_pass)
        tradeable = bool(ref_valid and depth_pass and live_corroborated)
        any_ref_valid |= ref_valid
        any_tradeable |= tradeable
        regimes[r] = {
            "parity_pass": parity_pass,
            "calibration_pass": calib_pass,
            "lead_pass": lead_pass,
            "leader": lead.get(r, {}).get("leader"),
            "kalshi_ece": calib.get(r, {}).get("kalshi", {}).get("ece"),
            "poly_ece": calib.get(r, {}).get("polymarket", {}).get("ece"),
            "n_divergences": lead.get(r, {}).get("n_divergences"),
            "reference_valid": ref_valid,
            "tradeable": tradeable,
        }

    if any_tradeable:
        verdict = "GO"
    elif any_ref_valid:
        verdict = "CONDITIONAL — reference valid but not yet tradeable"
    else:
        verdict = "KILL — no regime has a usable cross-venue reference"

    blockers = []
    if any_ref_valid and not any_tradeable:
        if not depth_pass:
            blockers.append("Polymarket depth below the $%.0f/side floor (capacity)"
                            % min_depth_usd)
        if not live_corroborated:
            blockers.append("no live-recorder corroboration (historical-only)")
        blockers.append("thin ~1-month sample (Polymarket price-history retention)")

    return {
        "gate": "G4", "verdict": verdict,
        "bounded_verdict_date": bounded_date,
        "regimes": regimes,
        "tradeability_blockers": blockers,
        "traces": {"census": census.get("artifact_path"),
                   "parity": parity.get("artifact_path"),
                   "calibration": calibration.get("artifact_path"),
                   "lead_lag": lead_lag.get("artifact_path")},
    }


def render(verdict: dict) -> str:
    lines = ["", "===== G4 FINAL VERDICT (map_winner, Kalshi as reference) =====",
             f"VERDICT: {verdict['verdict']}",
             f"bounded re-judgment date: {verdict['bounded_verdict_date']}", ""]
    for r, v in verdict["regimes"].items():
        lines.append(f"[{r}] reference_valid={v['reference_valid']}  tradeable={v['tradeable']}")
        lines.append(f"   parity={v['parity_pass']}  calibration={v['calibration_pass']}"
                     f" (Kalshi ECE {_f(v['kalshi_ece'])} vs Poly {_f(v['poly_ece'])})"
                     f"  lead={v['lead_pass']} (leader={v['leader']},"
                     f" n_div={v['n_divergences']})")
    if verdict["tradeability_blockers"]:
        lines.append("")
        lines.append("tradeability blockers (why not GO):")
        for b in verdict["tradeability_blockers"]:
            lines.append(f"   - {b}")
    lines.append("")
    lines.append("traces:")
    for k, path in verdict["traces"].items():
        lines.append(f"   {k}: {path}")
    return "\n".join(lines)


def _f(x):
    return "n/a" if x is None else f"{x:.4f}"
