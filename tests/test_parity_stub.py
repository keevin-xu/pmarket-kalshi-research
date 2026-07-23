"""Settlement-parity unit tests + lead-lag still-pending marker."""
import pytest

from core.parity import settlement
from core.reference import lead_lag


def _rec(teams, day, map_no, winner):
    return {"teams": teams, "ts": f"{day}T04:00:00.000Z", "map_no": map_no,
            "winner": winner}


def test_contract_claims_match_same_map_and_teams():
    poly = {"map_no": 1, "teams": ("Bilibili Gaming", "HLE"), "winner": "Bilibili Gaming"}
    kal = {"map_no": 1, "teams": ("Hanwha Life Esports", "Bilibili Gaming"),
           "winner": "Bilibili Gaming"}
    assert settlement.contract_claims_match(poly, kal)
    # different map number -> not the same claim
    assert not settlement.contract_claims_match({**poly, "map_no": 2}, kal)
    # contradictory settled winners -> not the same claim
    assert not settlement.contract_claims_match(
        poly, {**kal, "winner": "Hanwha Life Esports"})


def test_family_parity_agrees_and_passes():
    oe = [_rec(("Bilibili Gaming", "Hanwha Life Esports"), "2026-07-12", i,
               "Bilibili Gaming") for i in range(1, 41)]
    kal = [_rec(("Bilibili Gaming", "Hanwha Life Esports"), "2026-07-12", i,
                "Bilibili Gaming") for i in range(1, 41)]
    pol = [_rec(("BLG" if False else "Bilibili Gaming", "HLE"), "2026-07-12", i,
                "Bilibili Gaming") for i in range(1, 41)]
    res = settlement.check_family_parity(oe, kal, pol)
    assert res.n_aligned == 40 and res.n_agree == 40
    assert res.passed_gate and res.pass_rate == 1.0


def test_family_parity_void_break_fails():
    # OE has no map 2 (unplayed) but a venue resolved it -> void break
    oe = [_rec(("T1", "Gen.G"), "2026-07-10", 1, "T1")]
    kal = [_rec(("T1", "Gen.G"), "2026-07-10", 1, "T1"),
           _rec(("T1", "Gen.G"), "2026-07-10", 2, "T1")]  # map 2 never played
    pol = [_rec(("T1", "Gen.G"), "2026-07-10", 1, "T1")]
    res = settlement.check_family_parity(oe, kal, pol)
    assert res.n_void_breaks == 1 and not res.passed_gate


def test_divergence_detection_and_lead_sign():
    # Kalshi jumps to 0.70 at t=100 and holds; Polymarket lags at 0.50 then
    # converges up to 0.70 within the 300s window -> KALSHI LEADS.
    kalshi = [(0, 0.50), (100, 0.70), (200, 0.70), (300, 0.70), (400, 0.70)]
    poly = [(0, 0.50), (100, 0.50), (200, 0.50), (300, 0.50), (400, 0.70)]
    divs = lead_lag.detect_divergences(poly, kalshi, "in_game", match_id="m1")
    assert divs, "should detect a confirmed 0.20 gap"
    L = lead_lag.convergence_after(divs[0], poly, kalshi)
    assert L > 0  # follower (Polymarket) moved toward Kalshi => Kalshi leads


def test_no_divergence_below_threshold():
    kalshi = [(t, 0.51) for t in range(0, 500, 60)]
    poly = [(t, 0.50) for t in range(0, 500, 60)]  # 0.01 gap < 0.02 threshold
    assert lead_lag.detect_divergences(poly, kalshi, "in_game") == []
