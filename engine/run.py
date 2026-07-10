"""Orchestration + CLI. Runs the chain and STOPS at each gate for human
review — momentum must not carry a failed gate.

Chain: ingest -> G0 census -> G1 parity -> G2 calibration -> G3 lead-lag
-> G4 verdict. Each stage reads point-in-time via db.store only, writes a
stored artifact, and records discards with reason codes.
"""
from __future__ import annotations

import argparse


GATES = ["ingest", "G0", "G1", "G2", "G3", "G4"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pmarket-kalshi-research pipeline")
    ap.add_argument("--gate", choices=GATES, help="run a single gate")
    ap.add_argument("--db", default=None, help="override db path")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    raise NotImplementedError(
        f"dispatch gate={args.gate!r}; each gate stops for human review"
    )


if __name__ == "__main__":
    raise SystemExit(main())
