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
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from core.census import sweep
from core.config import CONFIG
from core.db import store
from core.ingest.base import VendorError, best_bid_ask, mid_or_none
from core.ingest.kalshi import KalshiAdapter
from core.ingest.polymarket import PolymarketAdapter

log = logging.getLogger("recorder")


def _cumulative_usd(levels: list[tuple[float, float]]) -> float | None:
    """Sum price*size across all levels ($ notional). None for an empty side."""
    return round(sum(p * s for p, s in levels), 2) if levels else None


def _ladder_json(archive: str, sides: dict, full_raw: dict | None) -> str | None:
    """Order-book archive per RecorderConfig.book_archive:
      'compact' -> the FULL ladder {side: [[price, size], ...]} minus vendor
                   metadata (preserves the price-impact curve at ~1/2-1/3 bytes),
      'full'    -> the verbose vendor JSON verbatim,
      'none'    -> nothing (parsed depth columns still recorded).
    """
    if archive == "none":
        return None
    if archive == "full":
        return json.dumps(full_raw or {}, separators=(",", ":"))
    return json.dumps({k: [[round(p, 6), s] for p, s in v] for k, v in sides.items()},
                      separators=(",", ":"))


def _regime(kickoff_ts: int | None, now: int) -> str | None:
    if kickoff_ts is None:
        return None
    return CONFIG.regimes.PRE_MATCH if now < kickoff_ts else CONFIG.regimes.IN_GAME


def parse_polymarket_book(raw: dict, contract_id: str, regime: str | None,
                          latency_ms: int, ts: str, archive: str = "compact") -> dict:
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
        "raw_json": _ladder_json(archive, {"bids": bids, "asks": asks}, raw),
    }


def parse_kalshi_market(market: dict, orderbook_raw: dict | None, regime: str | None,
                        latency_ms: int, ts: str, archive: str = "compact") -> dict:
    """Top-of-book from the VERIFIED market fields (yes_bid/ask_dollars, sizes).
    Full book from orderbook_fp is UNVERIFIED -> archived, parsed defensively
    (None if unrecognized)."""
    bid = market.get("yes_bid_dollars")
    ask = market.get("yes_ask_dollars")
    bid = None if bid is None else float(bid)
    ask = None if ask is None else float(ask)
    bsz = market.get("yes_bid_size_fp")
    asz = market.get("yes_ask_size_fp")
    two_sided = bid is not None and ask is not None
    yes: list[tuple[float, float]] = []
    no: list[tuple[float, float]] = []
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
            yes = no = []
    return {
        "contract_id": market.get("ticker"), "venue": CONFIG.venues.KALSHI, "ts": ts,
        "source": "live", "regime": regime, "fetch_latency_ms": latency_ms,
        "best_bid": bid, "best_ask": ask, "mid": mid_or_none(bid, ask),
        "top_bid_usd": round(bid * float(bsz), 2) if two_sided and bsz else None,
        "top_ask_usd": round(ask * float(asz), 2) if two_sided and asz else None,
        "full_bid_usd": full_bid, "full_ask_usd": full_ask, "n_levels": n_levels,
        "book_ok": 1 if two_sided else 0,
        "raw_json": _ladder_json(archive, {"yes": yes, "no": no}, orderbook_raw),
    }


class Recorder:
    """One recorder unit. poll_cycle() is idempotent + restart-safe; run() loops."""

    def __init__(self, conn, sport, kalshi: KalshiAdapter | None = None,
                 poly: PolymarketAdapter | None = None):
        self.conn = conn
        self.sport = sport
        self.kalshi = kalshi or KalshiAdapter()
        self.poly = poly or PolymarketAdapter()
        self._cooldown: dict[str, float] = {}   # venue -> unix cooldown_until
        self._catalog: dict | None = None
        self._catalog_at = 0.0
        self._cycle = 0                         # drives cap rotation
        self._nobook: dict[str, int] = {}       # market -> cycle it may be polled again

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
        sport = self.sport
        families = sport.params.census.families_phase1
        cat = {"polymarket": [], "kalshi": []}
        # Polymarket open phase-1 fixtures for THIS sport
        if not self._blocked("polymarket"):
            try:
                for ev in self.poly.iter_events(sport.polymarket_tag(), closed=False):
                    title = ev.get("title", "")
                    if sweep.parse_pm_title(title) is None:
                        continue
                    if CONFIG.recorder.tier1_only and not sport.is_tier1(title):
                        continue
                    kickoff = sweep.pm_match_dt(ev)
                    for m in ev.get("markets", []) or []:
                        text = f'{title} — {m.get("question","")}'
                        if sport.is_prop(text) or sport.classify_family(text) not in families:
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
        # Kalshi open phase-1 markets for THIS sport
        if not self._blocked("kalshi"):
            try:
                for series in sport.kalshi_series():
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

    # --- disk guard ----------------------------------------------------------
    def _archive_mode(self) -> str:
        """Configured archive mode, downgraded to 'none' if the DB filesystem
        is low on space — a full disk must never corrupt the DB."""
        mode = CONFIG.recorder.book_archive
        try:
            free_mb = shutil.disk_usage(Path(self.sport.params.db_path).parent).free / 1e6
        except OSError:
            return mode
        if free_mb < CONFIG.recorder.min_free_disk_mb:
            log.error("DISK-GUARD: %.0f MB free < %d MB -> archive='none' this cycle "
                      "(parsed depth still recorded)", free_mb, CONFIG.recorder.min_free_disk_mb)
            return "none"
        return mode

    # --- selection: rotate under the cap, rest markets with no book ----------
    def _eligible(self, fixtures: list[dict], key) -> list[dict]:
        """Drop markets currently resting after a no-book response."""
        return [f for f in fixtures if self._nobook.get(key(f), -1) <= self._cycle]

    def _select(self, fixtures: list[dict], venue: str) -> list[dict]:
        """At most `max_markets_per_cycle`, ROTATING across cycles.

        Slicing the head every cycle would poll the same first N markets
        forever and never once look at the tail — with a catalog well over the
        cap, that is a permanent, silent blind spot. Rotating guarantees every
        market is visited within ceil(len/cap) cycles.
        """
        cap = CONFIG.recorder.max_markets_per_cycle
        if len(fixtures) <= cap:
            return fixtures
        start = (self._cycle * cap) % len(fixtures)
        picked = (fixtures + fixtures)[start:start + cap]
        log.warning("CAP: %s catalog %d > cap %d — polling %d this cycle "
                    "(rotating, full sweep every %d cycles)",
                    venue, len(fixtures), cap, len(picked),
                    -(-len(fixtures) // cap))
        return picked

    def _rest(self, key: str) -> None:
        self._nobook[key] = self._cycle + CONFIG.recorder.nobook_cooldown_cycles

    # --- one cycle -----------------------------------------------------------
    def poll_cycle(self) -> int:
        cat = self._discover()
        rows: list[dict] = []
        dropped = 0
        gaps = 0                                   # per-market no-book 404s (normal)
        now = int(time.time())
        archive = self._archive_mode()

        pm_all = self._eligible(cat["polymarket"], lambda f: f["contract_id"])
        resting = len(cat["polymarket"]) - len(pm_all)
        pm = self._select(pm_all, "polymarket")
        dropped += len(pm_all) - len(pm)
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
                    gaps += 1                          # counted; summarized per cycle
                    self._rest(f["contract_id"])       # no book: stop burning budget on it
                    log.debug("gap: polymarket %s -> %r", f["contract_id"], e)
                    continue
                lat = int((time.monotonic() - t0) * 1000)
                ts = store.to_ts(datetime.now(timezone.utc))
                rows.append(parse_polymarket_book(raw, f["contract_id"],
                                                  _regime(f["kickoff"], now), lat, ts, archive))

        k_all = self._eligible(cat["kalshi"], lambda f: f["ticker"])
        resting += len(cat["kalshi"]) - len(k_all)
        k = self._select(k_all, "kalshi")
        dropped += len(k_all) - len(k)
        if not self._blocked("kalshi"):
            for f in k:
                t0 = time.monotonic()
                try:
                    ob = self.kalshi.get_orderbook(f["ticker"])
                except VendorError as e:
                    if e.is_refusal():
                        self._trip("kalshi", e)
                        break
                    dropped += 1
                    gaps += 1
                    self._rest(f["ticker"])
                    log.debug("gap: kalshi %s -> %r", f["ticker"], e)
                    continue
                lat = int((time.monotonic() - t0) * 1000)
                ts = store.to_ts(datetime.now(timezone.utc))
                rows.append(parse_kalshi_market(f["market"], ob,
                                                _regime(f["kickoff"], now), lat, ts, archive))

        self._cycle += 1
        cursor = store.to_ts(datetime.now(timezone.utc))
        n = store.upsert_book_snapshots_with_cursor(
            self.conn, rows, stream="recorder:cycle", cursor_value=cursor)
        log.info("cycle: %d snapshots written, %d dropped (%d no-book gaps, "
                 "%d resting, archive=%s)", n, dropped, gaps, resting, archive)
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


def run_recorder(sport, db_path: str | None = None, *, once: bool = False,
                 max_cycles: int | None = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    conn = store.connect(db_path or sport.params.db_path)
    store.init_schema(conn)
    rec = Recorder(conn, sport)
    rec.run(max_cycles=1 if once else max_cycles)


if __name__ == "__main__":
    import argparse
    from sports import get_sport
    ap = argparse.ArgumentParser(description="live dual-venue book recorder (observe-only)")
    ap.add_argument("--sport", default="lol")
    ap.add_argument("--db", default=None)
    ap.add_argument("--once", action="store_true", help="one cycle then exit (rehearsal)")
    ap.add_argument("--cycles", type=int, default=None)
    a = ap.parse_args()
    run_recorder(get_sport(a.sport), a.db, once=a.once, max_cycles=a.cycles)
