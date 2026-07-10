"""Kalshi adapter: trade-api v2 (markets, orderbook, candlesticks).

Kalshi is an ORDER BOOK, not a bookmaker: the two sides sum to ~1, there
is no vig. The reference price is the mid (or last) per config.reference.
DO NOT run Kalshi prices through any de-vig formula. Pin identifiers
(series/event/market tickers) the same way Polymarket ids are pinned.
"""
from __future__ import annotations

from ingest.base import Adapter
from config import CONFIG


class KalshiAdapter(Adapter):
    venue = CONFIG.venues.KALSHI

    def fetch(self, *args, **kwargs):
        raise NotImplementedError("wire Kalshi trade-api v2 reads; mock in tests")

    def to_quote_rows(self, payload) -> list[dict]:
        raise NotImplementedError("normalize Kalshi orderbook to quotes rows (mid/last)")
