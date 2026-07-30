"""CS2 classification. The texts are verbatim shapes observed at recon —
the point of these tests is that CS2's phrasings differ from LoL's, so a
classifier ported without change would misread every CS2 market.
"""
from sports.cs2 import population as pop


def test_map_winner_uses_map_not_game():
    # CS2 says "Map N"; LoL's "\bgame \d\b" would never fire here.
    assert pop.classify_family(
        "Counter-Strike: regain vs Chicken Coop Esports - Map 1 Winner") == "map_winner"
    assert pop.map_number("Counter-Strike: A vs B - Map 2 Winner") == 2
    assert pop.map_number("Counter-Strike: A vs B (BO3) - IEM Cologne") is None


def test_match_winner_is_the_event_title_with_a_bo_marker():
    # Polymarket's series moneyline question IS the event title. There is no
    # "Match Winner" phrasing to key on.
    title = "Counter-Strike: Voca vs SportsBetExpert (BO3) - StarLadder Playoffs"
    assert pop.classify_family(f"{title} — {title}") == "match_winner"
    assert pop.classify_family("Counter-Strike: A vs B (BO1) - ESEA Advanced") == "match_winner"


def test_map_wins_over_match_when_both_markers_present():
    # Sweeps hand over `event_title + question`, so a map market's text also
    # carries the title's "(BO3)". Order must resolve it to the map.
    text = ("Counter-Strike: A vs B (BO3) - IEM Cologne — "
            "Counter-Strike: A vs B - Map 1 Winner")
    assert pop.classify_family(text) == "map_winner"


def test_props_excluded():
    for q in ("Games Total: O/U 2.5",
              "Map Handicap: REGAIN (-1.5) vs Chicken Coop Esports (+1.5)",
              "Map 1 Total Rounds: Over/Under 21.5",
              "Map 1 Rounds Handicap: Spirit Academy (-3.5) vs aimclub (+3.5)"):
        assert pop.is_prop(q), q
        assert pop.classify_family(f"Counter-Strike: A vs B (BO3) - IEM — {q}") is None


def test_prop_markers_do_not_swallow_real_fixtures():
    # A team called MVP, or a tournament with "Championship" in the name, must
    # not be read as a player-award prop.
    assert not pop.is_prop("Counter-Strike: MVP vs NRG (BO3) - CS Asia Championships")
    assert pop.classify_family(
        "Counter-Strike: MVP vs NRG - Map 1 Winner") == "map_winner"


def test_tier1_prefers_the_neutral_field_over_any_text():
    # The neutral tier decides, even when the title looks tier-1 (a Major
    # qualifier) or looks minor (an S-tier event we never listed by name).
    assert pop.is_tier1("StarLadder StarSeries North American Qualifier", "s")
    assert not pop.is_tier1("Intel Extreme Masters Cologne Major 2026", "b")
    assert pop.is_tier1("some tournament we have never heard of", "s")
    assert not pop.is_tier1("CS Asia Championships 2026", "a")


def test_tier1_text_prefilter_when_no_neutral_tier():
    assert pop.is_tier1("Counter-Strike: A vs B (BO3) - IEM Cologne Major")
    assert not pop.is_tier1("Counter-Strike: A vs B (BO3) - CCT Europe Contenders #7")
    assert not pop.is_tier1("Counter-Strike: A vs B (BO3) - BLAST Bounty Closed Qualifier")
