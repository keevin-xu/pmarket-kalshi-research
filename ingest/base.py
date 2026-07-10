"""Base ingest contract. Adapters convert vendor payloads into store rows.

Rules enforced here so no adapter can quietly break them:
  * raise on naive datetimes (UTC discipline is a boundary concern),
  * store fixed-width ISO-8601 UTC via db.store.to_ts,
  * idempotent upsert on natural keys,
  * a swallowed HTTP error is FALSE DATA: check status before trusting a
    body; never turn a 429 into "zero rows".
  * order books may arrive worst->best; pin ordering per venue empirically.

All network calls go through `fetch`, which is monkeypatched to a mock in
tests. No test hits a live vendor.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


class VendorError(RuntimeError):
    """Raised on a non-OK response. Callers must NOT interpret this as empty."""


def require_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime at ingest boundary; supply tzinfo=UTC")
    return dt.astimezone(timezone.utc)


def best_bid_ask(bids: list[tuple[float, float]], asks: list[tuple[float, float]]):
    """Order-agnostic top of book. best bid = max price; best ask = min price.
    NEVER trust index 0. Returns (bid, ask, bid_size, ask_size) with Nones for
    a one-sided book (a gap is a gap, not a zero)."""
    bid = max(bids, key=lambda x: x[0]) if bids else None
    ask = min(asks, key=lambda x: x[0]) if asks else None
    return (
        bid[0] if bid else None,
        ask[0] if ask else None,
        bid[1] if bid else None,
        ask[1] if ask else None,
    )


def mid_or_none(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return round((bid + ask) / 2.0, 6)


class Adapter(ABC):
    """A venue/outcome adapter. `fetch` is the only network seam."""

    venue: str = "base"

    @abstractmethod
    def fetch(self, *args: Any, **kwargs: Any) -> Any:
        """Perform a single vendor call. Raise VendorError on non-OK.
        Mocked in tests."""

    @abstractmethod
    def to_quote_rows(self, payload: Any) -> list[dict]:
        """Normalize a payload into store `quotes` rows (dicts)."""
