"""Polymarket adapter: Gamma (discovery), CLOB (/book, /prices-history),
Data API. Reference price is the order-book mid/last (config.reference),
NEVER de-vigged. Book ladder ordering is pinned empirically via
base.best_bid_ask (do not trust index 0).

Discovery notes: markets nest inside events; /events?tag_slug=... paginate
by offset; know condition id (market), CLOB token ids (tradeable leg), slug.
Classify family/tier from question+event text BEFORE any statistic.

Schema pinned in DECISIONS.md [2026-07-12] NOTE: `outcomes`/`outcomePrices`
are JSON-STRING arrays; `bestBid`/`bestAsk`/`lastTradePrice` top-of-book;
`clobTokenIds` per outcome leg; per-side depth in $ needs CLOB `/book`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from config import CONFIG
from ingest.base import (HTTP_HEADERS, SSL_CONTEXT, Adapter, VendorError,
                         best_bid_ask, mid_or_none)

LOL_TAG = "league-of-legends"


def _maybe_json_list(v):
    """Gamma returns some list fields as JSON strings; decode defensively."""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return [v]
    return v or []


class PolymarketAdapter(Adapter):
    venue = CONFIG.venues.POLYMARKET

    def __init__(self, gamma: str | None = None, clob: str | None = None):
        self.gamma = (gamma or CONFIG.polymarket_gamma).rstrip("/")
        self.clob = (clob or CONFIG.polymarket_clob).rstrip("/")

    # --- network seam (mocked in tests) --------------------------------------
    def fetch(self, base: str, path: str, **params):
        """One GET. Raises VendorError on non-OK — never returns [] on error
        (a swallowed status is false data)."""
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{base}/{path.lstrip('/')}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    raise VendorError(f"polymarket {url} -> HTTP {resp.status}")
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise VendorError(f"polymarket {url} -> HTTP {e.code}", status=e.code) from e
        except urllib.error.URLError as e:
            raise VendorError(f"polymarket {url} -> {e.reason}", status=0) from e

    # --- discovery (Gamma, offset-paginated) ---------------------------------
    def iter_events(self, *, closed: bool | None = None, stop_before: str | None = None):
        """Yield LoL events newest-first across offset pages.

        Gamma offset pagination has a hard cap (~offset 2100 -> HTTP 422); we
        (a) stop once a whole page predates `stop_before` (an ISO date string;
        events are startDate-DESC), which normally ends the sweep before the
        cap, and (b) treat the 422 cap as end-of-data rather than a failure,
        so an out-of-window truncation degrades gracefully instead of lying."""
        offset = 0
        while True:
            try:
                page = self.fetch(self.gamma, "events", tag_slug=LOL_TAG,
                                 closed=(None if closed is None else str(closed).lower()),
                                 limit=100, offset=offset,
                                 order="startDate", ascending="false")
            except VendorError as e:
                if "422" in str(e):  # Gamma offset cap reached
                    self.pagination_capped = True
                    break
                raise
            if not page:
                break
            for ev in page:
                yield ev
            # stop if the OLDEST event on this page is already before the window
            if stop_before and page:
                oldest = min((e.get("startDate") or "" for e in page), default="")
                if oldest and oldest < stop_before:
                    break
            offset += len(page)
            if len(page) < 100:
                break

    pagination_capped = False

    # --- order book depth (CLOB /book by token id) ---------------------------
    def book(self, token_id: str) -> dict:
        return self.fetch(self.clob, "book", token_id=token_id)

    def prices_history(self, token_id: str, *, fidelity: int = 1) -> list[dict]:
        """Full price time-series for a CLOB token: [{t: unix, p: price}].
        p is P(this outcome). Used to snapshot a calibration price at a fixed
        instant (last point at-or-before the target)."""
        h = self.fetch(self.clob, "prices-history", market=token_id,
                       interval="max", fidelity=fidelity)
        return h.get("history", [])

    def to_quote_rows(self, payload: dict) -> list[dict]:
        """Normalize a CLOB /book snapshot to a quotes row for the YES leg.

        Book ladders may arrive worst->best: best bid = max price, best ask =
        min price (base.best_bid_ask). Depth per side $ = price * size(shares).
        One-sided book keeps NULL for the missing side (a gap is a gap).
        """
        from db import store
        bids = [(float(l["price"]), float(l["size"])) for l in payload.get("bids", [])]
        asks = [(float(l["price"]), float(l["size"])) for l in payload.get("asks", [])]
        bid, ask, bid_sz, ask_sz = best_bid_ask(bids, asks)
        bid_usd = None if (bid is None or bid_sz is None) else round(bid * bid_sz, 2)
        ask_usd = None if (ask is None or ask_sz is None) else round(ask * ask_sz, 2)
        ts = payload.get("_snapshot_ts") or datetime.now(timezone.utc)
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return [{
            "contract_id": payload["_contract_id"],
            "venue": self.venue,
            "ts": store.to_ts(ts),
            "source": payload.get("_source", "live"),
            "regime": payload.get("_regime"),
            "bid": bid, "ask": ask, "mid": mid_or_none(bid, ask),
            "last": payload.get("_last"),
            "bid_size_usd": bid_usd, "ask_size_usd": ask_usd,
        }]

    def to_contract_rows(self, event: dict) -> list[dict]:
        """Map an event's nested markets to `contracts` rows (one per market).
        family/tier classified from question+event text; token ids retained."""
        rows = []
        ev_title = event.get("title", "")
        for m in event.get("markets", []) or []:
            q = m.get("question", "")
            text = f"{ev_title} — {q}"
            outcomes = _maybe_json_list(m.get("outcomes"))
            tokens = _maybe_json_list(m.get("clobTokenIds"))
            rows.append({
                "contract_id": m.get("conditionId"),
                "venue": self.venue,
                "question_text": text,
                "outcomes": outcomes,
                "clob_token_ids": tokens,
                "best_bid": m.get("bestBid"),
                "best_ask": m.get("bestAsk"),
                "last": m.get("lastTradePrice"),
                "closed": m.get("closed"),
                "start_ts": m.get("startDate") or event.get("startDate"),
                "end_ts": m.get("endDate"),
            })
        return rows
