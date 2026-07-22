"""Neutral ground-truth outcomes + coverage spine.

THE ANSWER KEY. Match results come from a neutral source (Oracle's Elixir
CSV), NEVER a venue's own settlement. Also provides the neutral SCHEDULE
used to verify venue coverage (fuzzy team-name join within a time tolerance)
— odds/market feeds only list what they price and self-report ~100%
coverage, so coverage must be checked against neutral truth.

Oracle's Elixir is a manual/gated CSV download (per-year, from
oracleselixir.com/tools/downloads). The operator drops the file under
`data/raw/`; this adapter parses it. One row per player per game plus two
`position == 'team'` rows per game — we use the team rows. Map granularity:
each `gameid` is one map (the right grain for map_winner). `result == 1`
marks the winning team. Timestamps are documented-UTC but naive in the CSV
— we attach UTC at THIS boundary (the one documented exception).
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone

from ingest.base import Adapter

# OE league code -> our normalized tier-1 code (others pass through unchanged;
# tier-1 membership is decided by census.population.is_tier1 on the result).
LEAGUE_NORMALIZE = {
    "WLDs": "Worlds", "MSI": "MSI", "LCK": "LCK", "LPL": "LPL",
    "LEC": "LEC", "LCS": "LCS", "LTA": "LTA", "LTA N": "LTA", "LTA S": "LTA",
    "LCP": "LCP",
}


def _parse_oe_datetime(s: str) -> datetime:
    """OE 'date' is 'YYYY-MM-DD HH:MM:SS', documented UTC but naive.
    Attach UTC here — the single documented naive-boundary exception."""
    dt = datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


class OutcomesAdapter(Adapter):
    venue = "neutral"

    def fetch(self, path: str):
        """Read the local OE CSV. (No network: OE is a manual download.)
        Returns the raw team-level rows; a missing file raises, never []."""
        with open(path, newline="", encoding="utf-8") as f:
            return [r for r in csv.DictReader(f) if (r.get("position") or "").lower() == "team"]

    def to_quote_rows(self, payload):  # outcomes are not quotes
        raise TypeError("outcomes are not quotes; use to_match_rows")

    def to_match_rows(self, team_rows: list[dict]) -> list[dict]:
        """Group team rows by gameid into `matches` rows (map granularity).

        Each gameid has two team rows; the one with result=='1' won. A game
        with != 2 team rows or no clear winner is skipped and reported by the
        caller (a gap is information, never fabricated)."""
        by_game: dict[str, list[dict]] = {}
        for r in team_rows:
            by_game.setdefault(r.get("gameid", ""), []).append(r)

        out: list[dict] = []
        for gameid, rows in by_game.items():
            if not gameid or len(rows) != 2:
                continue
            winners = [r for r in rows if str(r.get("result", "")).strip() == "1"]
            if len(winners) != 1:
                continue
            team_a, team_b = rows[0].get("teamname"), rows[1].get("teamname")
            league = LEAGUE_NORMALIZE.get((rows[0].get("league") or "").strip(),
                                          (rows[0].get("league") or "").strip())
            try:
                start = _parse_oe_datetime(rows[0].get("date", ""))
            except (ValueError, KeyError):
                continue
            from db import store
            out.append({
                "match_id": gameid,               # OE gameid = one map
                "league": league,
                "team_a": team_a,
                "team_b": team_b,
                "start_ts": store.to_ts(start),
                "best_of": None,                  # OE per-map; series length not per-row
                "neutral_source": "oracles_elixir",
                "result_winner": winners[0].get("teamname"),
                "result_ts": store.to_ts(start),  # map result known at/after start
                "_map_number": rows[0].get("game"),
                "_gamelength": rows[0].get("gamelength"),  # seconds; for in-game checkpoint
            })
        return out
