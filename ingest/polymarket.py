"""Polymarket adapter: Gamma (discovery), CLOB (/book, /prices-history),
Data API. Reference price is the order-book mid/last (config.reference),
NEVER de-vigged. Book ladder ordering is pinned empirically via
base.best_bid_ask (do not trust index 0).

Discovery notes: markets nest inside events; /events?tag_slug=... paginate
by offset; know condition id (market), CLOB token ids (tradeable leg), slug.
Classify family/tier from question+event text BEFORE any statistic.
"""
from __future__ import annotations

from ingest.base import Adapter
from config import CONFIG


class PolymarketAdapter(Adapter):
    venue = CONFIG.venues.POLYMARKET

    def fetch(self, *args, **kwargs):
        raise NotImplementedError("wire Gamma/CLOB/Data reads; mock in tests")

    def to_quote_rows(self, payload) -> list[dict]:
        raise NotImplementedError("normalize CLOB /book snapshots to quotes rows")
