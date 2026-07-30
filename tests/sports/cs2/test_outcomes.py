"""bo3.gg neutral adapter. No test touches the network — the only network
method is `pull_window`, and the guard that protects it is exercised with a
stubbed transport.

The fixture mirrors shapes pinned at recon: nanosecond durations, clan names
that differ from team names, and an aborted map whose reported duration is
impossible for its round count.
"""
import json

import pytest

from core.ingest.base import VendorError
from sports.cs2 import Cs2Sport
from sports.cs2.outcomes import FilterIgnored, OutcomesAdapter, checkpoint_status

SNAPSHOT = {
    "_source": "bo3.gg",
    "_pulled_at": "2026-07-30T06:00:00.000Z",
    "matches": [{
        "id": 125445, "slug": "iowa-stormboar-vs-lag-gaming-30-07-2026",
        "team1_id": 1, "team2_id": 2, "winner_team_id": 2, "tournament_id": 9,
        "status": "finished", "bo_type": 3, "tier": "s", "tier_rank": 1,
        "start_date": "2026-07-30T01:00:00.000+00:00",
        "end_date": "2026-07-30T03:30:00.000+00:00",
    }],
    "games": [
        {   # a normal map: 42.4 min over 18 rounds
            "id": 179318, "match_id": 125445, "number": 1, "state": "done",
            "begin_at": "2026-07-30T01:05:00.000+00:00", "map_name": "de_inferno",
            "duration": 2_545_578_147_840, "rounds_count": 18,
            "winner_clan_name": "Lifes A Game", "loser_clan_name": "Iowa Stormboar",
        },
        {   # a short map that still ends BEFORE the checkpoint
            "id": 179319, "match_id": 125445, "number": 2, "state": "done",
            "begin_at": "2026-07-30T02:00:00.000+00:00", "map_name": "de_mirage",
            "duration": 1_000_000_000_000, "rounds_count": 14,
            "winner_clan_name": "Iowa Stormboar", "loser_clan_name": "Lifes A Game",
        },
        {   # aborted/mis-parsed: 72 s against 14 played rounds — impossible
            "id": 179320, "match_id": 125445, "number": 3, "state": "done",
            "begin_at": "2026-07-30T03:00:00.000+00:00", "map_name": "de_nuke",
            "duration": 72_000_000_000, "rounds_count": 14,
            "winner_clan_name": "Lifes A Game", "loser_clan_name": "Iowa Stormboar",
        },
    ],
    "teams": {"1": {"id": 1, "name": "Iowa Stormboar", "slug": "iowa-stormboar"},
              "2": {"id": 2, "name": "LAG", "slug": "lag-gaming"}},
    "tournaments": {"9": {"id": 9, "name": "Intel Extreme Masters Cologne Major 2026",
                          "tier": "s", "tier_rank": 1}},
}


@pytest.fixture
def archive(tmp_path):
    p = tmp_path / "bo3" / "window_20260730T060000000Z.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(SNAPSHOT))
    return [str(p)]


def test_match_rows_carry_tournament_tier_and_bo():
    rows = OutcomesAdapter().to_match_rows(SNAPSHOT)
    assert len(rows) == 1
    r = rows[0]
    assert r["team_a"] == "Iowa Stormboar" and r["team_b"] == "LAG"
    assert r["league"] == "Intel Extreme Masters Cologne Major 2026"
    assert r["best_of"] == 3 and r["_tier"] == "s"
    assert r["neutral_source"] == "bo3.gg"
    assert r["result_winner"] == "LAG"
    assert r["start_ts"] == "2026-07-30T01:00:00.000Z"      # fixed-width UTC


def test_map_results_are_per_map_anchored_on_begin_at():
    maps = OutcomesAdapter().to_map_results(SNAPSHOT)
    assert [m["map_no"] for m in maps] == [1, 2, 3]
    assert maps[0]["ts"] == "2026-07-30T01:05:00.000Z"      # map start, not match start
    assert maps[1]["ts"] == "2026-07-30T02:00:00.000Z"


def test_clan_name_resolves_to_the_team_name_or_stays_empty():
    maps = OutcomesAdapter().to_map_results(SNAPSHOT)
    assert maps[0]["winner"] == "LAG"                        # "Lifes A Game" -> LAG
    assert maps[1]["winner"] == "Iowa Stormboar"
    # an unrecognisable clan name is left unresolved, never guessed
    g = dict(SNAPSHOT["games"][0], winner_clan_name="some other org")
    snap = dict(SNAPSHOT, games=[g])
    assert OutcomesAdapter().to_map_results(snap)[0]["winner"] is None


def test_duration_is_nanoseconds_and_implausible_rows_report_no_length():
    oa = OutcomesAdapter()
    assert oa.map_length_s(SNAPSHOT["games"][0]) == 2545     # 2.5e12 ns -> s
    assert oa.map_length_s(SNAPSHOT["games"][1]) == 1000
    # 72 s over 14 rounds cannot have happened -> unknown, not a small number
    assert oa.map_length_s(SNAPSHOT["games"][2]) is None


def test_checkpoint_status_reason_codes():
    maps = {m["map_no"]: m for m in OutcomesAdapter().to_map_results(SNAPSHOT)}
    assert checkpoint_status(maps[1], 1200) == "ok"          # 2545 s map
    assert checkpoint_status(maps[2], 1200) == "checkpoint_after_map_end"
    assert checkpoint_status(maps[3], 1200) == "unreliable_map_length"
    assert checkpoint_status({"ts": None}, 1200) == "no_map_start"


def test_merge_prefers_the_newest_pull_and_is_idempotent():
    oa = OutcomesAdapter()
    newer = {"_pulled_at": "2026-07-31T00:00:00.000Z",
             "matches": [dict(SNAPSHOT["matches"][0], tier="a")],
             "games": [], "teams": SNAPSHOT["teams"],
             "tournaments": SNAPSHOT["tournaments"]}
    merged = oa.merge([SNAPSHOT, newer])
    assert oa.to_match_rows(merged)[0]["_tier"] == "a"
    assert oa.merge([SNAPSHOT, newer]) == oa.merge([newer, SNAPSHOT])


def test_a_filter_that_does_not_bind_raises_instead_of_sweeping_everything():
    """The vendor answers HTTP 200 with the FULL table for a filter it does
    not understand. Silently accepting that would swap a tier-1 population
    for all 78k CS2 matches ever played."""
    oa = OutcomesAdapter()
    oa._get = lambda resource, params: {"total": {"count": 78074}, "results": []}
    with pytest.raises(FilterIgnored):
        oa._assert_filter_bound("matches", {"filter[matches.tier][eq]": "s"})

    calls = []

    def narrowing(resource, params):
        calls.append(params)
        return {"total": {"count": 110 if len(params) > 1 else 78074}, "results": []}

    oa._get = narrowing
    assert oa._assert_filter_bound("matches", {"filter[matches.tier][eq]": "s"}) == 110


def test_incomplete_sweep_is_an_error_not_a_short_answer():
    oa = OutcomesAdapter()
    oa._get = lambda resource, params: {"total": {"count": 5},
                                        "results": [{"id": 1}, {"id": 2}]}
    with pytest.raises(VendorError):
        oa._paged("matches", {}, sort="start_date", expected_total=5)


def test_sport_loads_tier1_maps_from_the_archive(archive):
    sport = Cs2Sport()
    matches = sport.load_matches(archive)
    assert len(matches) == 1 and matches[0]["_tier"] == "s"

    maps = sport.load_map_results(archive)
    assert len(maps) == 3
    assert {sport.checkpoint_status(m) for m in maps} == {
        "ok", "checkpoint_after_map_end", "unreliable_map_length"}


def test_non_tier1_matches_are_dropped_by_the_neutral_field(archive, tmp_path):
    demoted = dict(SNAPSHOT, matches=[dict(SNAPSHOT["matches"][0], tier="b")])
    p = tmp_path / "bo3" / "window_20260731T060000000Z.json"
    p.write_text(json.dumps(dict(demoted, _pulled_at="2026-07-31T06:00:00.000Z")))
    sport = Cs2Sport()
    assert sport.load_matches([str(p)]) == []
    assert sport.load_map_results([str(p)]) == []
