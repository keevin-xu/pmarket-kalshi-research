"""Normalization unit tests for the ingest adapters. No network: payloads
are tiny synthetic fixtures matching the schemas pinned in DECISIONS.md."""
from __future__ import annotations

import csv

from census import population as pop
from ingest.kalshi import KalshiAdapter
from ingest.polymarket import PolymarketAdapter
from ingest.outcomes import OutcomesAdapter


# --- Kalshi: order book -> mid, NO de-vig, depth in $ --------------------------
def test_kalshi_quote_mid_no_devig():
    row = KalshiAdapter().to_quote_rows({
        "ticker": "KXLOLMAP-26JUL120400HLEBLG-1-BLG",
        "yes_bid_dollars": 0.60, "yes_ask_dollars": 0.64,
        "last_price_dollars": 0.62,
        "yes_bid_size_fp": 1000, "yes_ask_size_fp": 500,
        "_snapshot_ts": "2026-07-12T04:10:00Z", "_source": "hist",
    })[0]
    assert row["mid"] == 0.62            # (0.60+0.64)/2, not a de-vig
    assert row["bid_size_usd"] == 600.0  # 1000 * 0.60
    assert row["ask_size_usd"] == 320.0  # 500 * 0.64
    assert row["venue"] == "kalshi"


def test_kalshi_one_sided_book_is_gap_not_zero():
    row = KalshiAdapter().to_quote_rows({
        "ticker": "X", "yes_bid_dollars": 0.5, "yes_ask_dollars": None,
        "yes_bid_size_fp": 100, "yes_ask_size_fp": None,
        "_snapshot_ts": "2026-07-12T00:00:00Z",
    })[0]
    assert row["ask"] is None and row["ask_size_usd"] is None  # gap, not 0
    assert row["mid"] is None


# --- Polymarket: /book worst->best safe, depth in $ ---------------------------
def test_polymarket_book_best_of_ladder():
    payload = {
        "_contract_id": "0xabc", "_source": "hist",
        "bids": [{"price": "0.40", "size": "100"}, {"price": "0.55", "size": "200"}],
        "asks": [{"price": "0.70", "size": "50"}, {"price": "0.60", "size": "80"}],
        "_snapshot_ts": "2026-07-12T00:00:00Z",
    }
    row = PolymarketAdapter().to_quote_rows(payload)[0]
    assert row["bid"] == 0.55 and row["ask"] == 0.60   # max bid, min ask (not idx 0)
    assert row["bid_size_usd"] == 110.0                # 0.55 * 200
    assert row["ask_size_usd"] == 48.0                 # 0.60 * 80


# --- Oracle's Elixir: team rows -> matches rows -------------------------------
def test_oe_to_match_rows(tmp_path):
    csv_path = tmp_path / "oe.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["gameid", "league", "date", "game",
                                          "position", "side", "teamname", "result"])
        w.writeheader()
        w.writerow({"gameid": "G1", "league": "MSI", "date": "2026-07-12 04:00:00",
                    "game": "1", "position": "team", "side": "Blue",
                    "teamname": "Hanwha Life Esports", "result": "1"})
        w.writerow({"gameid": "G1", "league": "MSI", "date": "2026-07-12 04:00:00",
                    "game": "1", "position": "team", "side": "Red",
                    "teamname": "Bilibili Gaming", "result": "0"})
        # a player row (must be ignored by fetch)
        w.writerow({"gameid": "G1", "league": "MSI", "date": "2026-07-12 04:00:00",
                    "game": "1", "position": "top", "side": "Blue",
                    "teamname": "Hanwha Life Esports", "result": "1"})
    a = OutcomesAdapter()
    rows = a.to_match_rows(a.fetch(str(csv_path)))
    assert len(rows) == 1
    m = rows[0]
    assert m["result_winner"] == "Hanwha Life Esports"
    assert m["league"] == "MSI"
    assert m["start_ts"].endswith("Z") and m["neutral_source"] == "oracles_elixir"


# --- classifier extensions for the REAL venue phrasings -----------------------
def test_classifier_real_phrasings():
    assert pop.classify_family("LoL: BLG vs HLE - Game 1 Winner") == "map_winner"
    assert pop.classify_family("LoL: G2 vs T1 (BO5) - Match Result") == "series_winner"
    # MSI spelled out is tier-1; Prime League is NOT
    assert pop.is_tier1("BLG vs HLE (BO5) - Mid-Season Invitational Playoffs")
    assert not pop.is_tier1("VfB vs Eintracht (BO1) - Prime League 1st Division")
