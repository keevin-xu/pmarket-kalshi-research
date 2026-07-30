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


# --- G0 sample floor in maps/blocks (sports that set it) ----------------------
def test_map_and_block_floors_are_reported_and_judged():
    from core.sport import CensusParams
    from engine import run as engine_run

    cp = CensusParams(min_covered_maps=2, min_event_blocks=2,
                      event_block_unit="tournament")

    class _Sport:
        params = None

        def load_map_results(self, paths):
            return [
                {"teams": ("A", "B"), "ts": "2026-07-01T00:00:00.000Z", "map_no": 1,
                 "winner": "A", "match_id": "m1:m1", "_league": "IEM"},
                {"teams": ("A", "B"), "ts": "2026-07-01T00:00:00.000Z", "map_no": 2,
                 "winner": "B", "match_id": "m1:m2", "_league": "IEM"},
                {"teams": ("C", "D"), "ts": "2026-07-02T00:00:00.000Z", "map_no": 1,
                 "winner": "C", "match_id": "m2:m1", "_league": "BLAST"},
            ]

    venue_maps = [
        {"teams": ("A", "B"), "ts": "2026-07-01T00:00:00.000Z", "map_no": 1, "winner": "A"},
        {"teams": ("A", "B"), "ts": "2026-07-01T00:00:00.000Z", "map_no": 2, "winner": "B"},
        {"teams": ("C", "D"), "ts": "2026-07-02T00:00:00.000Z", "map_no": 1, "winner": "C"},
    ]
    engine_run.sweep.sweep_kalshi_map_results = lambda s: list(venue_maps)
    engine_run.sweep.sweep_polymarket_map_results = lambda s: list(venue_maps[:2])

    out = engine_run._map_coverage(_Sport(), [], cp)
    assert out["n_covered_maps"] == 2                 # map 3 has one venue only
    assert out["blocks"] == {"match": 1, "tournament": 1}
    assert out["passes_maps"] is True                 # 2 >= 2
    assert out["passes_blocks"] is False              # 1 tournament < 2


def test_map_floor_is_inert_for_sports_that_did_not_set_it():
    from core.sport import CensusParams
    from engine import run as engine_run
    assert engine_run._map_coverage(None, [], CensusParams()) == {"applies": False}
