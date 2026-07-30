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

import logging
import ssl
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("ingest")

try:  # a proper CA bundle — some Python builds ship no system certs
    import certifi
    SSL_CONTEXT: ssl.SSLContext | None = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover
    SSL_CONTEXT = ssl.create_default_context()

# Some venue gateways (Gamma) 403 the default Python-urllib UA; send a real one.
HTTP_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "pmarket-kalshi-research/0.1 (measurement; +research)",
}


class VendorError(RuntimeError):
    """Raised on a non-OK response. Callers must NOT interpret this as empty.

    `status` carries the HTTP code (0 = transport/SSL/timeout) so callers can
    tell a REFUSAL (429 / 5xx / transport → circuit-break) from a per-item GAP
    (e.g. 404 'no book for this token' → skip that item, keep recording)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    def is_refusal(self) -> bool:
        s = self.status
        return s == 0 or s == 429 or (s is not None and 500 <= s < 600)


class Refused(VendorError):
    """A vendor refused repeatedly (429/5xx/transport). Distinct from a plain
    VendorError so a per-item handler cannot mistake a sustained outage for
    "this market simply has no data" — that swallow writes the outage into the
    archive as a fact about the market."""


class Pacer:
    """Self-imposed request spacing for BULK reads (backfill, census sweeps).

    Neither venue publishes a rate-limit or Retry-After header, so our own
    spacing is the only protection. Kalshi refused at ~7 req/s during recon
    and was clean at ~3.
    """

    def __init__(self, interval_s: float):
        self.interval_s = interval_s
        self._last = 0.0

    def wait(self) -> None:
        gap = self.interval_s - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def with_retries(fn, *, pacer: Pacer, venue: str, what: str,
                 max_tries: int = 4, backoff_s: float = 15.0):
    """Run a paced vendor call, backing off on refusals and FAILING LOUDLY if
    they persist. Never returns empty on refusal — a 429 is an outage, and
    reporting it as "no rows" is how a rate limit becomes a research verdict.

    For bulk readers only. The recorder deliberately does NOT use this: it
    wants a refusal to trip its per-venue breaker immediately and keep the
    other venue recording.
    """
    for attempt in range(max_tries):
        pacer.wait()
        try:
            return fn()
        except VendorError as e:
            if not e.is_refusal():
                raise                            # 404 etc. = a per-item gap
            if attempt == max_tries - 1:
                break
            wait = backoff_s * (attempt + 1)
            log.warning("REFUSAL %s %s (%r) — backing off %.0fs (%d/%d)",
                        venue, what, e, wait, attempt + 1, max_tries)
            time.sleep(wait)
    raise Refused(f"{venue} {what}: refused {max_tries} times; stopping rather "
                  f"than reporting an outage as no data")


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
