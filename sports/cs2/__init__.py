"""CS2 as a plugin over the sport-agnostic core. Implements the `Sport`
interface: classification (population.py), discovery (Polymarket tag + Kalshi
tickers), neutral outcomes (bo3.gg, read from the local archive), and its OWN
proposed params. Nothing here inherits or touches LoL's frozen numbers.

Phase 1 is `map_winner` on maps 1-2 only: Polymarket lists no Map 3 Winner
and no map market at all on Bo1 events, so a Kalshi map-3 row has no
counterparty. `match_winner` is a separate pre-registered phase 2 — the
tickers are listed here for discovery, but `families()` reports phase 1 only,
so no gate can pool them.
"""
from __future__ import annotations

from core.db import store
from core.sport import SportParams
from sports.cs2 import params as P
from sports.cs2 import population as pop
from sports.cs2.outcomes import OutcomesAdapter, archive_paths, checkpoint_status
from sports.cs2.params import CS2_PARAMS


class Cs2Sport:
    key = "cs2"
    params: SportParams = CS2_PARAMS

    # --- population classification -------------------------------------------
    def classify_family(self, text: str) -> str | None:
        return pop.classify_family(text)

    def is_prop(self, text: str) -> bool:
        return pop.is_prop(text)

    def is_tier1(self, text: str, league: str | None = None) -> bool:
        """`league` carries the NEUTRAL source's tier code ("s"/"a"/…) for CS2,
        which is authoritative. With no tier — i.e. the live recorder, which
        only ever sees a venue title — this is True by design, so recording
        captures WIDE and the gates tier-filter offline. A live book missed is
        a live book gone."""
        return pop.is_tier1(text, league)

    def map_number(self, text: str) -> int | None:
        """CS2 says "Map N" where LoL says "Game N"."""
        return pop.map_number(text)

    # --- venue discovery -----------------------------------------------------
    def polymarket_tag(self) -> str:
        # `cs2` / `counter-strike` carry futures and novelty markets; the
        # per-match events live under this tag (pinned at recon).
        return "counter-strike-2"

    def kalshi_series(self) -> dict[str, str]:
        # KXCS2MAP = per-map winner (phase 1). KXCS2GAME = full-match winner —
        # it DOES settle for CS2 (unlike LoL's KXLOL), and is phase 2.
        return {"KXCS2MAP": "map_winner", "KXCS2GAME": "match_winner"}

    # --- neutral ground truth (bo3.gg, from the local archive) ---------------
    def outcome_paths(self) -> list[str]:
        return archive_paths(self.params.raw_dir)

    def _merged(self, paths: list[str]) -> dict:
        oa = OutcomesAdapter()
        return oa.merge([oa.fetch(p) for p in paths])

    def load_matches(self, paths: list[str]) -> list[dict]:
        """Archived bo3.gg snapshots -> tier-1, in-window `matches` (the
        neutral spine). Tier comes from the neutral field, never from text."""
        oa = OutcomesAdapter()
        win = store.from_ts(self.params.census.window_start)
        out: list[dict] = []
        for m in oa.to_match_rows(self._merged(paths)):
            if not self.is_tier1(m["league"], m["_tier"]):
                continue
            if store.from_ts(m["start_ts"]) < win:
                continue
            out.append(m)
        return out

    def load_all_matches(self, paths: list[str]) -> list[dict]:
        """Every archived fixture in the window, tier UNFILTERED.

        Used only to target the history capture: pulling a market's series is
        irreversible-if-skipped, so targeting must not be narrowed by a tier
        ruling that a later gate might revisit.
        """
        oa = OutcomesAdapter()
        win = store.from_ts(self.params.census.window_start)
        return [m for m in oa.to_match_rows(self._merged(paths))
                if store.from_ts(m["start_ts"]) >= win]

    def backfill_neutral(self) -> str:
        """Pull + archive the neutral window (tier s and the secondary a arm).

        bo3.gg keeps full history, so this is the one source that CAN be
        re-pulled; the venues cannot, which is why their capture is wide.
        """
        oa = OutcomesAdapter()
        start = store.from_ts(self.params.census.window_start).strftime("%Y-%m-%dT%H:%M:%SZ")
        return oa.pull_window(window_start=start,
                              tiers=(pop.TIER1_CODE,) + tuple(P.TIER_SECONDARY_ARM),
                              archive_dir=self.params.raw_dir)

    def load_map_results(self, paths: list[str]) -> list[dict]:
        """Tier-1, in-window PLAYED maps: {teams, ts (map start), map_no,
        winner, match_id, gamelen_s}."""
        oa = OutcomesAdapter()
        win = store.from_ts(self.params.census.window_start)
        keep = {m["match_id"] for m in self.load_matches(paths)}
        out: list[dict] = []
        for r in oa.to_map_results(self._merged(paths)):
            if r["match_id"].rsplit(":m", 1)[0] not in keep:
                continue
            if store.from_ts(r["ts"]) < win:
                continue
            out.append(r)
        return out

    # --- in-game checkpoint --------------------------------------------------
    def checkpoint_status(self, map_record: dict) -> str:
        """"ok" or a discard reason code for this map's in-game checkpoint
        (`games.begin_at + params.reference.in_game_checkpoint_s`)."""
        return checkpoint_status(map_record, self.params.reference.in_game_checkpoint_s)

    # --- parity --------------------------------------------------------------
    def families(self) -> tuple[str, ...]:
        return self.params.census.families_phase1
