"""The recorder loop is shared and unchanged; what CS2 must prove is that its
plugin feeds that loop correctly — CS2 market phrasings are accepted, props
are not, and a fixture whose venue title does not reveal its tier is still
recorded rather than dropped.

That last one is the expensive direction: bo3.gg can be re-pulled, a live
order book cannot.
"""
from __future__ import annotations

import pytest

from core.db import store
from core.ingest import record
from sports.cs2 import Cs2Sport

SPORT = Cs2Sport()

# A real CS2 event shape: tier-3 tournament in the title, map + match markets
# plus the props that must never be recorded.
PM_EVENT = {
    "title": "Counter-Strike: Voca vs SportsBetExpert (BO3) - CCT Europe Contenders #7",
    "slug": "cs2-voc-spo-2026-07-30",
    "startDate": "2026-07-30T03:04:05Z",
    "markets": [
        {"question": "Counter-Strike: Voca vs SportsBetExpert - Map 1 Winner",
         "conditionId": "0xmap1", "clobTokenIds": ["tok1", "tok2"]},
        {"question": "Games Total: O/U 2.5",
         "conditionId": "0xtotal", "clobTokenIds": ["tok3", "tok4"]},
        {"question": "Map Handicap: VOCA (-1.5) vs SportsBetExpert (+1.5)",
         "conditionId": "0xhcap", "clobTokenIds": ["tok5", "tok6"]},
    ],
}


class FakePoly:
    venue = "polymarket"

    def iter_events(self, tag, *, closed=None, stop_before=None):
        assert tag == "counter-strike-2"          # the tag pinned at recon
        return [PM_EVENT]

    def book(self, token):
        return {"bids": [{"price": "0.48", "size": "120"}],
                "asks": [{"price": "0.53", "size": "80"}]}


class FakeKalshi:
    venue = "kalshi"

    def list_events(self, series, status=None, cursor=None):
        if series == "KXCS2MAP":
            return {"events": [{"markets": [
                {"ticker": "KXCS2MAP-26JUL301800VOCSPO-1-VOC", "status": "active",
                 "yes_bid_dollars": 0.47, "yes_ask_dollars": 0.52,
                 "yes_bid_size_fp": 300, "yes_ask_size_fp": 200,
                 "close_time": "2026-07-30T23:00:00Z"}]}]}
        return {"events": []}

    def get_orderbook(self, ticker):
        return {"orderbook_fp": {}}


@pytest.fixture
def conn(tmp_path):
    c = store.connect(str(tmp_path / "rec.db"))
    store.init_schema(c)
    return c


def test_cycle_records_both_venues_for_cs2(conn):
    n = record.Recorder(conn, SPORT, FakeKalshi(), FakePoly()).poll_cycle()
    assert n == 2
    venues = {r[0] for r in conn.execute("SELECT DISTINCT venue FROM book_snapshots")}
    assert venues == {"polymarket", "kalshi"}


def test_a_title_that_hides_its_tier_is_still_recorded(conn):
    """CCT Europe is tier-c, but the recorder cannot know that from a title —
    and it must not guess. The row is captured; the gates drop it later using
    the neutral tier field."""
    rec = record.Recorder(conn, SPORT, FakeKalshi(), FakePoly())
    cat = rec._discover()
    assert [f["contract_id"] for f in cat["polymarket"]] == ["0xmap1"]


def test_props_never_reach_the_recorder(conn):
    cat = record.Recorder(conn, SPORT, FakeKalshi(), FakePoly())._discover()
    recorded = {f["contract_id"] for f in cat["polymarket"]}
    assert "0xtotal" not in recorded and "0xhcap" not in recorded


def test_only_phase1_families_are_recorded(conn):
    """`families_phase1` is map_winner, so the series moneyline is discovered
    but not recorded — adding match_winner later needs no recorder change."""
    cat = record.Recorder(conn, SPORT, FakeKalshi(), FakePoly())._discover()
    assert SPORT.families() == ("map_winner",)
    assert len(cat["polymarket"]) == 1


def test_kickoff_comes_from_the_slug_date_not_the_listing_date(conn):
    cat = record.Recorder(conn, SPORT, FakeKalshi(), FakePoly())._discover()
    kickoff = cat["polymarket"][0]["kickoff"]
    assert kickoff is not None
    # slug says 2026-07-30; startDate is the listing instant and must not win
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(kickoff, timezone.utc).date().isoformat() == "2026-07-30"
