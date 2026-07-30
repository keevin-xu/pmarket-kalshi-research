"""CS2 same-claim edge cases, driven by what recon found the two venues
actually promise:

  * an uncompleted map resolves **50-50 on Polymarket** and to the **fair
    market price on Kalshi** — different payoffs, and neither is a winner;
  * **Polymarket lists no Map 3 Winner**, so a Kalshi map-3 row has no
    counterparty at all.

Both must leave the parity number alone rather than manufacturing a
disagreement — a settlement difference read as a mismatch is the fake-edge
failure this gate exists to prevent.
"""
from core.census import sweep
from core.parity.settlement import check_family_parity
from core.sport import ParityParams

PARITY = ParityParams(min_family_pass_rate=0.95, min_aligned_maps=2)
TEAMS = ("Iowa Stormboar", "LAG")


def _rec(map_no, winner, ts="2026-07-30T01:05:00.000Z"):
    return {"teams": TEAMS, "ts": ts, "map_no": map_no, "winner": winner}


# maps 1-2 played; map 3 never happened (2-0 Bo3)
NEUTRAL = [_rec(1, "LAG"), _rec(2, "Iowa Stormboar", "2026-07-30T02:00:00.000Z")]


def test_agreement_on_the_two_maps_both_venues_price():
    kalshi = [_rec(1, "LAG"), _rec(2, "Iowa Stormboar", "2026-07-30T02:00:00.000Z")]
    poly = [_rec(1, "LAG"), _rec(2, "Iowa Stormboar", "2026-07-30T02:00:00.000Z")]
    res = check_family_parity(NEUTRAL, kalshi, poly, PARITY)
    assert res.n_aligned == 2 and res.n_agree == 2
    assert res.n_void_breaks == 0 and res.passed_gate


def test_kalshi_map3_without_a_polymarket_counterparty_is_not_a_disagreement():
    # Kalshi lists map 3 and settles it to the fair market price -> no winner.
    kalshi = [_rec(1, "LAG"), _rec(2, "Iowa Stormboar", "2026-07-30T02:00:00.000Z"),
              _rec(3, None, "2026-07-30T03:00:00.000Z")]
    poly = [_rec(1, "LAG"), _rec(2, "Iowa Stormboar", "2026-07-30T02:00:00.000Z")]
    res = check_family_parity(NEUTRAL, kalshi, poly, PARITY)
    assert res.n_aligned == 2                      # map 3 never enters the population
    assert res.n_void_breaks == 0 and res.passed_gate


def test_a_venue_naming_a_winner_on_an_unplayed_map_is_a_void_break():
    kalshi = [_rec(1, "LAG"), _rec(2, "Iowa Stormboar", "2026-07-30T02:00:00.000Z"),
              _rec(3, "LAG", "2026-07-30T03:00:00.000Z")]
    poly = [_rec(1, "LAG"), _rec(2, "Iowa Stormboar", "2026-07-30T02:00:00.000Z")]
    res = check_family_parity(NEUTRAL, kalshi, poly, PARITY)
    assert res.n_void_breaks == 1 and not res.passed_gate


def test_polymarket_50_50_resolution_is_not_a_winner():
    """"If Map N is not completed for any reason, this market will resolve
    50-50" — both legs at 0.5, so no outcome may be read off it."""
    assert sweep._pm_winner({"outcomes": '["Iowa Stormboar", "LAG"]',
                             "outcomePrices": '["0.5", "0.5"]'}) is None
    assert sweep._pm_winner({"outcomes": '["Iowa Stormboar", "LAG"]',
                             "outcomePrices": '["0", "1"]'}) == "LAG"
