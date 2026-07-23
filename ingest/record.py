"""Live dual-venue recorder: continuous FULL-book snapshots of BOTH venues,
source='live'. Built to the Recorder Field Guide. It OBSERVES ONLY — never
places orders, moves funds, or holds keys beyond read access. Execution lives
in a separate repo that does not exist yet.

Integrity properties (all from the guide):
  * restart-safe: the cycle cursor is committed ATOMICALLY with the rows it
    covers (db.store.upsert_book_snapshots_with_cursor); a re-run over the
    same input changes nothing (natural key upsert).
  * TRUE timestamps + per-row fetch latency, never intended values.
  * gaps are gaps: a one-sided/empty/failed book stores NULLs + book_ok=0,
    never zeros; a refused HTTP call is an outage, not "zero rows".
  * 429 / vendor refusal arms a per-venue cooldown (no blind retry); degrade
    and log loudly.
  * catalog of open fixtures memoized with a TTL; not re-fetched every cycle.
  * FULL book captured (top-of-book + cumulative $/side) with the raw payload
    archived verbatim.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from census import population as pop
from census import sweep
from config import CONFIG
from db import store
from ingest.base import VendorError, best_bid_ask, mid_or_none
from ingest.kalshi import KalshiAdapter, PHASE1_SERIES
from ingest.polymarket import PolymarketAdapter

log = logging.getLogger("recorder")


def _cumulative_usd(levels: list[tuple[float, float]]) -> float | None:
    """Sum price*size across all levels ($ notional). None for an empty side."""
    return round(sum(p * s for p, s in levels), 2) if levels else None


def _regime(kickoff_ts: int | None, now: int) -> str | None:
    if kickoff_ts is None:
        return None
    return CONFIG.regimes.PRE_MATCH if now < kickoff_ts else CONFIG.regimes.IN_GAME


def parse_polymarket_book(raw: dict, contract_id: str, regime: str | None,
                          latency_ms: int, ts: str) -> dict:
    """Verified schema: raw['bids'|'asks'] = [{price,size}]. Ladder may arrive
    worst-first -> best via max(bids)/min(asks). Full book cumulative."""
    bids = [(float(l["price"]), float(l["size"])) for l in raw.get("bids", [])]
    asks = [(float(l["price"]), float(l["size"])) for l in raw.get("asks", [])]
    bid, ask, bsz, asz = best_bid_ask(bids, asks)
    two_sided = bid is not None and ask is not None
    return {
        "contract_id": contract_id, "venue": CONFIG.venues.POLYMARKET, "ts": ts,
        "source": "live", "regime": regime, "fetch_latency_ms": latency_ms,
        "best_bid": bid, "best_ask": ask, "mid": mid_or_none(bid, ask),
        "top_bid_usd": round(bid * bsz, 2) if two_sided and bsz else None,
        "top_ask_usd": round(ask * asz, 2) if two_sided and asz else None,
        "full_bid_usd": _cumulative_usd(bids), "full_ask_usd": _cumulative_usd(asks),
        "n_levels": len(bids) + len(asks), "book_ok": 1 if two_sided else 0,
        "raw_json": json.dumps(raw, separators=(",", ":")),
    }


def parse_kalshi_market(market: dict, orderbook_raw: dict | None, regime: str | None,
                        latency_ms: int, ts: str) -> dict:
    """Top-of-book from the VERIFIED market fields (yes_bid/ask_dollars, sizes).
    Full book from orderbook_fp is UNVERIFIED -> archived raw, parsed defensively
    (None if unrecognized)."""
    bid = market.get("yes_bid_dollars")
    ask = market.get("yes_ask_dollars")
    bid = None if bid is None else float(bid)
    ask = None if ask is None else float(ask)
    bsz = market.get("yes_bid_size_fp")
    asz = market.get("yes_ask_size_fp")
    two_sided = bid is not None and ask is not None
    full_bid = full_ask = n_levels = None
    if not CONFIG.recorder.kalshi_orderbook_verified:
        ob = (orderbook_raw or {}).get("orderbook_fp") or {}
        # defensive: only trust if it looks like {'yes':[[p,s]...], 'no':[...]}
        try:
            yes = [(float(p), float(s)) for p, s in (ob.get("yes") or [])]
            no = [(float(p), float(s)) for p, s in (ob.get("no") or [])]
            if yes or no:
                full_bid, full_ask = _cumulative_usd(yes), _cumulative_usd(no)
                n_levels = len(yes) + len(no)
        except (TypeError, ValueError):
            pass
    return {
        "contract_id": market.get("ticker"), "venue": CONFIG.venues.KALSHI, "ts": ts,
        "source": "live", "regime": regime, "fetch_latency_ms": latency_ms,
        "best_bid": bid, "best_ask": ask, "mid": mid_or_none(bid, ask),
        "top_bid_usd": round(bid * float(bsz), 2) if two_sided and bsz else None,
        "top_ask_usd": round(ask * float(asz), 2) if two_sided and asz else None,
        "full_bid_usd": full_bid, "full_ask_usd": full_ask, "n_levels": n_levels,
        "book_ok": 1 if two_sided else 0,
        "raw_json": json.dumps(orderbook_raw or {}, separators=(",", ":")),
    }


class Recorder:
    """One recorder unit. poll_cycle() is idempotent + restart-safe; run() loops."""

    def __init__(self, conn, kalshi: KalshiAdapter | None = None,
                 poly: PolymarketAdapter | None = None):
        self.conn = conn
        self.kalshi = kalshi or KalshiAdapter()
        self.poly = poly or PolymarketAdapter()
        self._cooldown: dict[str, float] = {}   # venue -> unix cooldown_until
        self._catalog: dict | None = None
        self._catalog_at = 0.0

    # --- circuit breaker -----------------------------------------------------
    def _blocked(self, venue: str) -> bool:
        return time.time() < self._cooldown.get(venue, 0)

    def _trip(self, venue: str, err: Exception) -> None:
        until = time.time() + CONFIG.recorder.cooldown_s
        self._cooldown[venue] = until
        log.error("CIRCUIT-BREAK %s for %ds after refusal: %r",
                  venue, CONFIG.recorder.cooldown_s, err)
        store.log_spend(self.conn, venue, "*", 0, note=f"cooldown: {err!r}")

    # --- discovery (memoized catalog) ----------------------------------------
    def _discover(self) -> dict:
        now = time.time()
        if self._catalog is not None and now - self._catalog_at < CONFIG.recorder.catalog_ttl_s:
            return self._catalog
        cat = {"polymarket": [], "kalshi": []}
        # Polymarket open LoL phase-1 fixtures
        if not self._blocked("polymarket"):
            try:
                for ev in self.poly.iter_events(closed=False):
                    title = ev.get("title", "")
                    if sweep.parse_pm_title(title) is None:
                        continue
                    if CONFIG.recorder.tier1_only and not pop.is_tier1(title):
                        continue
                    kickoff = sweep.pm_match_dt(ev)
                    for m in ev.get("markets", []) or []:
                        text = f'{title} — {m.get("question","")}'
                        if pop.is_prop(text) or pop.classify_family(text) not in CONFIG.census.families_phase1:
                            continue
                        toks = m.get("clobTokenIds")
                        if isinstance(toks, str):
                            try:
                                toks = json.loads(toks)
                            except ValueError:
                                toks = []
                        cid = m.get("conditionId")
                        if cid and toks:
                            cat["polymarket"].append(
                                {"contract_id": cid, "token": toks[0],
                                 "kickoff": int(kickoff.timestamp()) if kickoff else None})
            except VendorError as e:
                self._trip("polymarket", e)
        # Kalshi open LoL phase-1 markets
        if not self._blocked("kalshi"):
            try:
                for series in PHASE1_SERIES:
                    page = self.kalshi.list_events(series, status="open")
                    for ev in page.get("events", []):
                        for m in ev.get("markets", []) or []:
                            if m.get("status") != "active":
                                continue
                            k = m.get("close_time")
                            cat["kalshi"].append(
                                {"ticker": m.get("ticker"), "market": m,
                                 "kickoff": None})   # kickoff via OE join later
            except VendorError as e:
                self._trip("kalshi", e)
        self._catalog, self._catalog_at = cat, now
        log.info("catalog: %d Polymarket + %d Kalshi open fixtures",
                 len(cat["polymarket"]), len(cat["kalshi"]))
        return cat

    # --- one cycle -----------------------------------------------------------
    def poll_cycle(self) -> int:
        cat = self._discover()
        rows: list[dict] = []
        dropped = 0
        now = int(time.time())

        pm = cat["polymarket"][:CONFIG.recorder.max_markets_per_cycle]
        if len(cat["polymarket"]) > CONFIG.recorder.max_markets_per_cycle:
            dropped += len(cat["polymarket"]) - len(pm)
            log.warning("CAP: dropped %d Polymarket markets this cycle",
                        len(cat["polymarket"]) - len(pm))
        if not self._blocked("polymarket"):
            for f in pm:
                t0 = time.monotonic()
                try:
                    raw = self.poly.book(f["token"])
                except VendorError as e:
                    if e.is_refusal():                 # 429/5xx/transport -> stop venue
                        self._trip("polymarket", e)
                        break
                    dropped += 1                       # 404 etc. = per-market gap
                    log.warning("gap: polymarket %s -> %r", f["contract_id"], e)
                    continue
                lat = int((time.monotonic() - t0) * 1000)
                ts = store.to_ts(datetime.now(timezone.utc))
                rows.append(parse_polymarket_book(raw, f["contract_id"],
                                                  _regime(f["kickoff"], now), lat, ts))

        if not self._blocked("kalshi"):
            for f in cat["kalshi"][:CONFIG.recorder.max_markets_per_cycle]:
                t0 = time.monotonic()
                try:
                    ob = self.kalshi.get_orderbook(f["ticker"])
                except VendorError as e:
                    if e.is_refusal():
                        self._trip("kalshi", e)
                        break
                    dropped += 1
                    log.warning("gap: kalshi %s -> %r", f["ticker"], e)
                    continue
                lat = int((time.monotonic() - t0) * 1000)
                ts = store.to_ts(datetime.now(timezone.utc))
                rows.append(parse_kalshi_market(f["market"], ob,
                                                _regime(f["kickoff"], now), lat, ts))

        cursor = store.to_ts(datetime.now(timezone.utc))
        n = store.upsert_book_snapshots_with_cursor(
            self.conn, rows, stream="recorder:cycle", cursor_value=cursor)
        log.info("cycle: %d snapshots written, %d dropped", n, dropped)
        return n

    def run(self, *, max_cycles: int | None = None) -> None:
        i = 0
        while max_cycles is None or i < max_cycles:
            try:
                self.poll_cycle()
            except Exception:                     # a bad cycle must not kill the unit
                log.exception("cycle failed; continuing")
            i += 1
            if max_cycles is None or i < max_cycles:
                time.sleep(CONFIG.recorder.poll_interval_s)


def run_recorder(db_path: str | None = None, *, once: bool = False,
                 max_cycles: int | None = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    conn = store.connect(db_path)
    store.init_schema(conn)
    rec = Recorder(conn)
    rec.run(max_cycles=1 if once else max_cycles)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="live dual-venue LoL book recorder (observe-only)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--once", action="store_true", help="one cycle then exit (rehearsal)")
    ap.add_argument("--cycles", type=int, default=None)
    a = ap.parse_args()
    run_recorder(a.db, once=a.once, max_cycles=a.cycles)
