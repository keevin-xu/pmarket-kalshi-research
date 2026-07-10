"""GATE G4 — verdict-first reporting.

Lead with the verdict against the PRE-REGISTERED rule, then the number, its
n, its CI, and caveat flags (hist-bounds, censoring, fallbacks used,
reference-validation status, settlement-parity coverage). An instrument
failure/outage is not a market result and is reported explicitly. Every
number must trace to a stored artifact under data/artifacts/ (run_artifacts).
"""
from __future__ import annotations


def build_verdict(census: dict, parity: dict, calibration: dict, lead_lag: dict) -> dict:
    """Combine gate outputs into a go/no-go: is there a (regime, direction)
    where the leader is ALSO the calibrated reference and the follower has
    depth to trade? If sample is short, defer to the bounded verdict date
    rather than kill/pass early."""
    raise NotImplementedError("assemble verdict against frozen DECISIONS.md rules")


def render(verdict: dict) -> str:
    raise NotImplementedError("verdict-first human-readable + machine artifact")
