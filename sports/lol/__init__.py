"""LoL as a plugin over the sport-agnostic core. Implements the `Sport`
interface: classification (population.py), discovery (Polymarket tag + Kalshi
tickers), neutral outcomes (Oracle's Elixir), and its OWN frozen params.
"""
from __future__ import annotations

import glob
from pathlib import Path

from core.db import store
from core.sport import SportParams
from sports.lol import population as pop
from sports.lol.outcomes import OutcomesAdapter
from sports.lol.params import LOL_PARAMS


class LolSport:
    key = "lol"
    params: SportParams = LOL_PARAMS

    # --- population classification -------------------------------------------
    def classify_family(self, text: str) -> str | None:
        return pop.classify_family(text)

    def is_prop(self, text: str) -> bool:
        return pop.is_prop(text)

    def is_tier1(self, text: str, league: str | None = None) -> bool:
        return pop.is_tier1(text)

    # --- venue discovery -----------------------------------------------------
    def polymarket_tag(self) -> str:
        return "league-of-legends"

    def kalshi_series(self) -> dict[str, str]:
        # KXLOL = per-match series winner (0 settled at recon); KXLOLMAP = map winner
        return {"KXLOL": "series_winner", "KXLOLMAP": "map_winner"}

    # --- neutral ground truth (Oracle's Elixir) ------------------------------
    def outcome_paths(self) -> list[str]:
        return sorted(glob.glob(str(Path(self.params.raw_dir) / "oracleselixer" / "202[56].csv")))

    def load_matches(self, paths: list[str]) -> list[dict]:
        """OE CSVs -> tier-1, in-window `matches` (the neutral spine)."""
        oa = OutcomesAdapter()
        win = store.from_ts(self.params.census.window_start)
        out: list[dict] = []
        for p in paths:
            for m in oa.to_match_rows(oa.fetch(p)):
                if m["league"] not in self.params.census.tier1_leagues:
                    continue
                if store.from_ts(m["start_ts"]) < win:
                    continue
                out.append(m)
        return out

    def load_map_results(self, paths: list[str]) -> list[dict]:
        """Tier-1, in-window OE PLAYED maps: {teams, ts, map_no, winner, gamelen_s}."""
        out: list[dict] = []
        for m in self.load_matches(paths):
            try:
                mno = int(m.get("_map_number"))
            except (TypeError, ValueError):
                continue
            try:
                gamelen = int(float(m.get("_gamelength")))
            except (TypeError, ValueError):
                gamelen = None
            out.append({"teams": (m["team_a"], m["team_b"]), "ts": m["start_ts"],
                        "map_no": mno, "winner": m["result_winner"],
                        "match_id": m["match_id"], "gamelen_s": gamelen})
        return out

    # --- parity --------------------------------------------------------------
    def families(self) -> tuple[str, ...]:
        return self.params.census.families_phase1
