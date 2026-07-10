"""GATE G1 — cross-venue settlement parity.

No number crosses venues until the two contracts are proven the SAME CLAIM
per market family. This is a first-class gate, not a footnote: a comparison
across non-identical claims is a mapping bug that turns a settlement
difference into a fake edge.

Per family, an offline test must confirm:
  * same map count / series length,
  * same void-vs-resolve rules (forfeits, no-shows, unplayed maps/legs),
  * same resolution SOURCE and TIMING,
  * conditional-on-played conventions match.
A family passes only with a stored test; non-passing families are excluded
from G2-G4 and recorded in DECISIONS.md. Sets contracts.parity_ok.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParityResult:
    family: str
    n_checked: int
    n_pass: int
    pass_rate: float
    passed_gate: bool


def check_family_parity(conn, family: str) -> ParityResult:
    """Evaluate same-claim parity for all matched contracts in a family,
    set contracts.parity_ok, and return the aggregate vs the frozen
    ParityConfig.min_family_pass_rate."""
    raise NotImplementedError("implement per-family same-claim mapping test")


def contract_claims_match(poly_contract: dict, kalshi_contract: dict) -> bool:
    """Offline predicate: do these two contracts settle on the identical
    event with identical void/resolution semantics? Pure function; unit-tested
    per family with fixtures."""
    raise NotImplementedError("encode family-specific settlement comparison")
