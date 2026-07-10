from census import population as pop


def test_props_excluded():
    assert pop.classify_family("Any player to get a Penta Kill?") is None
    assert pop.is_prop("First blood in game 1?")


def test_series_and_map_families():
    assert pop.classify_family("Will T1 win the series vs GEN?") == "series_winner"
    assert pop.classify_family("Winner of Map 2: T1 vs GEN") == "map_winner"


def test_tier1_exclusion_list_checked_first():
    assert pop.is_tier1("LCK Summer: T1 vs GEN")
    assert not pop.is_tier1("LCK Challengers: sister squads")
