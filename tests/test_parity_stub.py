"""Parity + lead-lag logic is not implemented yet; these mark the gate as
pending so the suite documents what still needs building."""
import pytest

from parity import settlement
from reference import lead_lag


def test_parity_not_yet_implemented():
    with pytest.raises(NotImplementedError):
        settlement.contract_claims_match({}, {})


def test_divergence_detection_not_yet_implemented():
    with pytest.raises(NotImplementedError):
        lead_lag.detect_divergences(None, None, "in_game")
