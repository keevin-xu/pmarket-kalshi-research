"""Neutral ground-truth outcomes + coverage.

THE ANSWER KEY. Match results come from a neutral source (Oracle's Elixir
CSV or PandaScore results), NEVER a venue's own settlement. Also provides
the neutral SCHEDULE used to verify venue coverage (fuzzy team-name join
within a time tolerance) — odds/market feeds only list what they price and
self-report ~100% coverage, so coverage must be checked against neutral truth.
"""
from __future__ import annotations

from ingest.base import Adapter


class OutcomesAdapter(Adapter):
    venue = "neutral"

    def fetch(self, *args, **kwargs):
        raise NotImplementedError("load Oracle's Elixir CSV / PandaScore results; mock in tests")

    def to_quote_rows(self, payload) -> list[dict]:
        raise NotImplementedError("outcomes are not quotes; write matches rows instead")

    def to_match_rows(self, payload) -> list[dict]:
        raise NotImplementedError("normalize neutral results into matches rows (result_winner, result_ts)")
