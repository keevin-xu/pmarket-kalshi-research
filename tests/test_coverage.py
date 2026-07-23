"""Coverage-join unit tests: fuzzy team matching (abbreviations, suffixes)
and the both-venues n_covered gate. Deterministic, no network."""
from __future__ import annotations

from core.census import coverage as cov
from sports.lol.params import LOL_PARAMS

CP = LOL_PARAMS.census


def test_team_match_abbreviations_and_suffixes():
    assert cov.team_match("JD Gaming", "JDG")            # prefix of full compact
    assert cov.team_match("Anyone's Legend", "AL")       # acronym
    assert cov.team_match("Hanwha Life Esports", "HLE")  # acronym
    assert cov.team_match("Gen.G", "Gen.G Esports")      # suffix-strip
    assert cov.team_match("Hanwha Life Esports", "Hanwha Life")
    assert cov.team_match("T1", "T1")
    assert not cov.team_match("T1", "Gen.G")
    assert not cov.team_match("Bilibili Gaming", "Top Esports")


def test_pair_match_order_agnostic():
    assert cov.pair_match(("Bilibili Gaming", "Hanwha Life Esports"),
                          ("Hanwha Life", "Bilibili Gaming"))
    assert not cov.pair_match(("Bilibili Gaming", "HLE"), ("T1", "Gen.G"))


def test_coverage_requires_both_venues():
    oe = [
        {"match_id": "g1", "team_a": "Bilibili Gaming", "team_b": "Hanwha Life Esports",
         "start_ts": "2026-07-12T04:00:00.000Z", "league": "MSI"},
        {"match_id": "g2", "team_a": "G2 Esports", "team_b": "T1",
         "start_ts": "2026-07-08T09:00:00.000Z", "league": "MSI"},
    ]
    kalshi = [
        {"teams": ("Bilibili Gaming", "Hanwha Life Esports"),
         "ts": "2026-07-12T04:30:00.000Z", "family": "map_winner", "contract_id": "k1"},
        {"teams": ("G2", "T1"),
         "ts": "2026-07-08T09:20:00.000Z", "family": "map_winner", "contract_id": "k2"},
    ]
    poly = [  # only covers g1 (Bilibili vs HLE), NOT g2
        {"teams": ("Bilibili Gaming", "HLE"),
         "ts": "2026-07-12T04:00:00.000Z", "family": "map_winner", "contract_id": "p1"},
    ]
    rep = cov.coverage_report(oe, {"kalshi": kalshi, "polymarket": poly}, CP)
    assert rep["n_tier1_series"] == 2
    assert rep["n_covered"] == 1          # only g1 is on BOTH venues
    assert "g1" in rep["covered_match_ids"] and "g2" not in rep["covered_match_ids"]
    assert rep["per_family_covered"]["map_winner"] == 1


def test_series_collapse_counts_once():
    # two OE maps, same teams same day -> one series
    oe = [
        {"match_id": "m1", "team_a": "T1", "team_b": "Gen.G",
         "start_ts": "2026-07-10T08:00:00.000Z", "league": "LCK"},
        {"match_id": "m2", "team_a": "T1", "team_b": "Gen.G",
         "start_ts": "2026-07-10T09:00:00.000Z", "league": "LCK"},
    ]
    v = [{"teams": ("T1", "Gen.G"), "ts": "2026-07-10T08:30:00.000Z",
          "family": "map_winner", "contract_id": "x"}]
    rep = cov.coverage_report(oe, {"kalshi": v, "polymarket": v}, CP)
    assert rep["n_tier1_series"] == 1
    assert rep["n_covered"] == 1
