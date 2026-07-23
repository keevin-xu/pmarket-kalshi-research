"""Kalshi adapter: trade-api v2 (series, events, markets, candlesticks).

Kalshi is an ORDER BOOK, not a bookmaker: the two sides sum to ~1, there
is no vig. The reference price is the mid (or last) per config.reference.
DO NOT run Kalshi prices through any de-vig formula. Pin identifiers
(series/event/market tickers) the same way Polymarket ids are pinned.

Schema pinned in DECISIONS.md [2026-07-12] NOTE (first pull, no auth):
prices are `*_dollars` in [0,1]; sizes are `*_size_fp` (fixed-point
contracts); `liquidity_dollars` is book notional. Reads are public; a key
is deferred (see DECISIONS.md). A non-OK response is an ERROR, never
"zero rows" (a swallowed 429 is false data).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from core.config import CONFIG
from core.ingest.base import HTTP_HEADERS, SSL_CONTEXT, Adapter, VendorError, mid_or_none

def _fp(x) -> float | None:
    """Kalshi fixed-point ints are already whole contracts / cents helpers.
    Sizes come as integer contract counts; return float or None."""
    return None if x is None else float(x)


class KalshiAdapter(Adapter):
    venue = CONFIG.venues.KALSHI

    def __init__(self, base: str | None = None):
        self.base = (base or CONFIG.kalshi_base).rstrip("/")

    # --- network seam (mocked in tests) --------------------------------------
    def fetch(self, path: str, **params) -> dict:
        """One GET against trade-api v2. Raises VendorError on non-OK.
        NEVER returns {} on error — a swallowed status is false data."""
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}/{path.lstrip('/')}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    raise VendorError(f"kalshi {url} -> HTTP {resp.status}")
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise VendorError(f"kalshi {url} -> HTTP {e.code}", status=e.code) from e
        except urllib.error.URLError as e:
            raise VendorError(f"kalshi {url} -> {e.reason}", status=0) from e

    # --- discovery -----------------------------------------------------------
    def list_events(self, series_ticker: str, *, status: str | None = None,
                    cursor: str | None = None) -> dict:
        """One page of events (with nested markets) for a series."""
        return self.fetch("events", series_ticker=series_ticker,
                          with_nested_markets="true", status=status,
                          limit=200, cursor=cursor)

    def iter_markets(self, series_ticker: str, *, status: str | None = None):
        """Yield (event, market) across all pages. Cursor-paginated; a page
        cap can't hide rows because we page by the API's own cursor."""
        cursor = None
        while True:
            page = self.list_events(series_ticker, status=status, cursor=cursor)
            for ev in page.get("events", []):
                for mkt in ev.get("markets", []) or []:
                    yield ev, mkt
            cursor = page.get("cursor") or None
            if not cursor:
                break

    def get_orderbook(self, market_ticker: str) -> dict:
        """Raw order book for a market. Schema (`orderbook_fp`) is UNVERIFIED
        against a live non-empty LoL book at build time — archived verbatim by
        the recorder; parse defensively until a live match pins it."""
        return self.fetch(f"markets/{market_ticker}/orderbook")

    # --- candlesticks (historical price series) ------------------------------
    def candlesticks(self, series_ticker: str, market_ticker: str,
                     start_ts: int, end_ts: int, period_min: int = 1) -> list[dict]:
        r = self.fetch(
            f"series/{series_ticker}/markets/{market_ticker}/candlesticks",
            start_ts=start_ts, end_ts=end_ts, period_interval=period_min,
        )
        return r.get("candlesticks", [])

    @staticmethod
    def candle_mid_at(candles: list[dict], target_ts: int) -> float | None:
        """Order-book MID (NO de-vig) from the last candle at-or-before
        target_ts: (yes_bid.close + yes_ask.close)/2. None if no two-sided
        candle exists yet (a gap is a gap)."""
        best = None
        for c in candles:
            if c.get("end_period_ts", 0) > target_ts:
                break
            best = c
        if best is None:
            return None
        try:
            bid = float(best["yes_bid"]["close_dollars"])
            ask = float(best["yes_ask"]["close_dollars"])
        except (KeyError, TypeError, ValueError):
            return None
        if bid <= 0 and ask <= 0:
            return None
        return round((bid + ask) / 2.0, 6)

    # --- normalization -------------------------------------------------------
    def to_quote_rows(self, payload: dict) -> list[dict]:
        """Normalize a market object (top-of-book snapshot) to a quotes row.

        Order book -> mid = (yes_bid+yes_ask)/2, NO de-vig. Depth per side in
        USD ~= size(contracts) * price(dollars). A one-sided book keeps NULL
        for the missing side (a gap is a gap, never a zero).
        """
        mkt = payload
        bid = mkt.get("yes_bid_dollars")
        ask = mkt.get("yes_ask_dollars")
        last = mkt.get("last_price_dollars")
        bid = None if bid is None else float(bid)
        ask = None if ask is None else float(ask)
        last = None if last is None else float(last)
        bid_ct = _fp(mkt.get("yes_bid_size_fp"))
        ask_ct = _fp(mkt.get("yes_ask_size_fp"))
        # depth in USD: contracts settle $1, so notional ~= size * price
        bid_usd = None if (bid_ct is None or bid is None) else round(bid_ct * bid, 2)
        ask_usd = None if (ask_ct is None or ask is None) else round(ask_ct * ask, 2)
        ts = mkt.get("_snapshot_ts") or datetime.now(timezone.utc)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        from core.db import store  # local import: keep store the single ts encoder
        return [{
            "contract_id": mkt["ticker"],
            "venue": self.venue,
            "ts": store.to_ts(ts),
            "source": mkt.get("_source", "live"),
            "regime": mkt.get("_regime"),
            "bid": bid, "ask": ask, "mid": mid_or_none(bid, ask), "last": last,
            "bid_size_usd": bid_usd, "ask_size_usd": ask_usd,
        }]
