"""One-shot capture of vendor history into the local store. SPORT-AGNOSTIC.

Why this exists, and why it runs before the gates: both venues forget.
Kalshi serves market-level rows (prices, candles, settlement) for a rolling
~68 days — older events survive as empty shells with zero markets — and
Polymarket's price history is ~30 days. Whatever is not captured today is
not recoverable tomorrow, so the local store becomes the system of record
and a re-fetch can only ever return LESS than the run before it.

What this is NOT: it computes no coverage, depth, parity, calibration or
lead-lag number. Ingesting is not measuring — the archive is write-only
until the relevant gate is frozen. The only numbers printed are ingestion
telemetry (rows written, refusals, discards by reason).

Capture policy:
  * **Catalogues are captured WIDE** — every settled market in the window on
    both venues, whatever its tier — because a later change to the tier or
    family definition cannot re-fetch what was never pulled.
  * **Series are captured by SCOPE** (`neutral` by default): the per-market
    price series is fetched for markets that join to a neutral fixture, which
    is a capture-targeting device, not a population decision. `--scope all`
    pulls every market's series (hours, and the only fully future-proof
    option).
  * Raw vendor payloads are archived verbatim under the sport's raw dir, so a
    parser bug or a schema change can be re-parsed rather than re-fetched.
  * Re-runs are cheap and idempotent: a market whose series is already in the
    store is skipped, and every write is an upsert on a natural key.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.census import coverage as cov
from core.census import sweep
from core.config import CONFIG
from core.db import store
from core.ingest.base import VendorError
from core.ingest.kalshi import KalshiAdapter
from core.ingest.polymarket import PolymarketAdapter

log = logging.getLogger("backfill")

STAGES = ("neutral", "kalshi", "polymarket")


class Refused(VendorError):
    """The vendor refused repeatedly (429/5xx/transport). Distinct from a
    plain VendorError so a per-item handler cannot mistake a sustained outage
    for "this market simply has no history" — that swallow would write the
    outage into the archive as a fact about the market."""


class Pacer:
    """Self-imposed request spacing. Neither venue publishes a rate-limit or
    Retry-After header, so the only safe pacing is our own."""

    def __init__(self, interval_s: float):
        self.interval_s = interval_s
        self._last = 0.0

    def wait(self) -> None:
        gap = self.interval_s - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


def _with_retries(fn, *, pacer: Pacer, venue: str, what: str, conn=None):
    """Run a vendor call, backing off on refusals and FAILING LOUDLY if they
    persist. A refusal is an outage; returning empty here would write a gap
    into the archive as though the vendor had said "nothing happened"."""
    cfg = CONFIG.backfill
    for attempt in range(cfg.max_refusal_retries):
        pacer.wait()
        try:
            return fn()
        except VendorError as e:
            if conn is not None:
                store.log_spend(conn, venue, what, e.status or 0, note=repr(e))
            if not e.is_refusal():
                raise                                   # 404 etc. = a per-item gap
            backoff = cfg.refusal_backoff_s * (attempt + 1)
            log.warning("REFUSAL %s %s (%r) — backing off %.0fs (%d/%d)",
                        venue, what, e, backoff, attempt + 1, cfg.max_refusal_retries)
            time.sleep(backoff)
    raise Refused(f"{venue} {what}: refused {cfg.max_refusal_retries} times; "
                  f"stopping rather than recording an outage as empty history")


class Backfill:
    def __init__(self, conn, sport, *, kalshi: KalshiAdapter | None = None,
                 poly: PolymarketAdapter | None = None, dry_run: bool = False):
        self.conn = conn
        self.sport = sport
        self.kalshi = kalshi or KalshiAdapter()
        self.poly = poly or PolymarketAdapter()
        self.dry_run = dry_run
        self.k_pace = Pacer(CONFIG.backfill.kalshi_min_interval_s)
        self.p_pace = Pacer(CONFIG.backfill.polymarket_min_interval_s)
        self._neutral_idx: dict[str, list[dict]] | None = None

    # --- archive -------------------------------------------------------------
    def _archive(self, venue: str, name: str, payload) -> None:
        """Write a raw vendor payload verbatim. Never overwrites: each pull is
        its own file, because a later pull may legitimately return less."""
        out = Path(self.sport.params.raw_dir) / "backfill" / venue
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        (out / f"{name}_{stamp}.json").write_text(json.dumps(payload, separators=(",", ":")))

    # --- neutral targeting ---------------------------------------------------
    def _neutral_index(self) -> dict[str, list[dict]]:
        """(day -> neutral fixtures) used ONLY to decide which markets are
        worth a series pull. Uses the sport's UNFILTERED neutral loader where
        it has one, so targeting is never narrower than the population a later
        tier ruling might want."""
        if self._neutral_idx is not None:
            return self._neutral_idx
        paths = self.sport.outcome_paths()
        loader = getattr(self.sport, "load_all_matches", None) or self.sport.load_matches
        idx: dict[str, list[dict]] = {}
        for m in loader(paths):
            idx.setdefault(m["start_ts"][:10], []).append(m)
        self._neutral_idx = idx
        log.info("neutral targeting index: %d fixtures across %d days",
                 sum(len(v) for v in idx.values()), len(idx))
        return idx

    def _is_targeted(self, teams: tuple[str, str], ts: str) -> bool:
        idx = self._neutral_index()
        day = datetime.strptime(ts[:10], "%Y-%m-%d")
        for shift in (-1, 0, 1):
            for m in idx.get((day + timedelta(days=shift)).strftime("%Y-%m-%d"), []):
                if cov.pair_match((m["team_a"], m["team_b"]), teams):
                    return True
        return False

    def _has_series(self, contract_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM quotes WHERE contract_id=? AND source='hist' LIMIT 1",
            [contract_id]).fetchone()
        return row is not None

    # --- stage: neutral ------------------------------------------------------
    def neutral(self) -> dict:
        """Pull the sport's neutral schedule/results if it has an API-backed
        source. Sports whose neutral source is a manual file are a no-op here."""
        pull = getattr(self.sport, "backfill_neutral", None)
        if pull is None:
            return {"skipped": "neutral source is not API-backed for this sport"}
        if self.dry_run:
            return {"would_pull": "neutral window"}
        path = pull()
        log.info("neutral archive written: %s", path)
        return {"archive": path}

    # --- stage: Kalshi -------------------------------------------------------
    def kalshi_history(self, *, scope: str = "neutral", limit: int | None = None) -> dict:
        """Settled markets (wide) + their candlestick series (by scope)."""
        win = self.sport.params.census.window_start
        n_events = n_contracts = n_rows = 0
        targets: list[dict] = []
        for series, family in self.sport.kalshi_series().items():
            cursor = None
            while True:
                page = _with_retries(
                    lambda s=series, c=cursor: self.kalshi.list_events(s, status="settled", cursor=c),
                    pacer=self.k_pace, venue=CONFIG.venues.KALSHI,
                    what=f"events/{series}", conn=self.conn)
                events = page.get("events") or []
                if not events:
                    break
                self._archive("kalshi", f"events_{series}", page)
                stop = False
                for ev in events:
                    mkts = ev.get("markets") or []
                    if len(mkts) < 2:
                        continue                     # shell event: retention already took it
                    close = mkts[0].get("close_time") or ""
                    ts = _iso_ts(close)
                    if ts is None:
                        continue
                    if ts < win:
                        stop = True                  # events are newest-first
                        continue
                    n_events += 1
                    teams = tuple(m.get("yes_sub_title") for m in mkts[:2])
                    rows = [{
                        "contract_id": m.get("ticker"), "venue": CONFIG.venues.KALSHI,
                        "match_id": None, "family": family,
                        "outcome_side": m.get("yes_sub_title") or "",
                        "question_text": ev.get("title") or "", "parity_ok": None,
                    } for m in mkts if m.get("ticker")]
                    if not self.dry_run:
                        n_contracts += store.upsert_contracts(self.conn, rows)
                    if not all(teams):
                        continue
                    if scope == "all" or self._is_targeted(teams, ts):
                        for m in mkts:
                            if m.get("ticker"):
                                targets.append({"series": series, "market": m, "close_ts": ts})
                cursor = page.get("cursor") or None
                if stop or not cursor:
                    break

        if limit:
            targets = targets[:limit]
        log.info("kalshi: %d settled events in window, %d markets queued for candles",
                 n_events, len(targets))
        if self.dry_run:
            return {"events": n_events, "candle_calls_planned": len(targets)}

        skipped = 0
        for t in targets:
            ticker = t["market"]["ticker"]
            if self._has_series(ticker):
                skipped += 1
                continue
            open_ts = _unix(t["market"].get("open_time")) or (_unix(t["market"].get("close_time")) or 0)
            close_ts = _unix(t["market"].get("close_time")) or open_ts
            try:
                candles = _with_retries(
                    lambda: self.kalshi.candlesticks(
                        t["series"], ticker,
                        open_ts - CONFIG.backfill.preroll_s,
                        close_ts + CONFIG.backfill.postroll_s,
                        CONFIG.backfill.candle_period_min),
                    pacer=self.k_pace, venue=CONFIG.venues.KALSHI,
                    what=f"candles/{ticker}", conn=self.conn)
            except Refused:
                raise                                # sustained outage: stop the stage
            except VendorError as e:
                store.record_discard(self.conn, "backfill", "no_candles",
                                     contract_id=ticker)
                log.debug("gap: kalshi candles %s -> %r", ticker, e)
                continue
            self._archive("kalshi", f"candles_{ticker}", candles)
            rows = kalshi_candle_rows(ticker, candles)
            if not rows:
                store.record_discard(self.conn, "backfill", "empty_candles",
                                     contract_id=ticker)
                continue
            n_rows += store.upsert_quotes(self.conn, rows)
        return {"events": n_events, "contracts": n_contracts, "quote_rows": n_rows,
                "already_captured": skipped, "candle_targets": len(targets)}

    # --- stage: Polymarket ---------------------------------------------------
    def polymarket_history(self, *, scope: str = "neutral", limit: int | None = None) -> dict:
        """Closed events (wide) + per-market price series (by scope).

        One series per market: the CLOB is binary, so the second leg is 1-p.
        `contracts.outcome_side` records WHICH outcome the stored price is the
        probability of — the analysis must not assume it is team_a.
        """
        win = self.sport.params.census.window_start
        families = set(self.sport.params.census.families_phase1) | {"match_winner"}
        n_events = n_contracts = n_rows = 0
        targets: list[dict] = []
        for ev in self.poly.iter_events(self.sport.polymarket_tag(), closed=True,
                                        stop_before=win):
            pair = sweep.parse_pm_title(ev.get("title", ""))
            t = sweep.pm_match_dt(ev)
            if pair is None or t is None:
                continue
            ts = store.to_ts(t)
            if ts < win:
                continue
            n_events += 1
            self._archive("polymarket", f"event_{ev.get('slug') or ev.get('id')}", ev)
            for m in ev.get("markets") or []:
                text = f'{ev.get("title","")} — {m.get("question","")}'
                fam = self.sport.classify_family(text)
                cid = m.get("conditionId")
                if self.sport.is_prop(text) or fam not in families or not cid:
                    continue
                tokens = sweep._json_list(m.get("clobTokenIds"))
                outcomes = sweep._json_list(m.get("outcomes"))
                if not tokens:
                    continue
                if not self.dry_run:
                    n_contracts += store.upsert_contracts(self.conn, [{
                        "contract_id": cid, "venue": CONFIG.venues.POLYMARKET,
                        "match_id": None, "family": fam,
                        "outcome_side": (outcomes[0] if outcomes else ""),
                        "question_text": text, "parity_ok": None}])
                if scope == "all" or self._is_targeted(pair, ts):
                    targets.append({"contract_id": cid, "token": tokens[0]})

        if limit:
            targets = targets[:limit]
        log.info("polymarket: %d closed events in window, %d markets queued for history",
                 n_events, len(targets))
        if self.dry_run:
            return {"events": n_events, "history_calls_planned": len(targets)}

        skipped = 0
        for t in targets:
            if self._has_series(t["contract_id"]):
                skipped += 1
                continue
            try:
                hist = _with_retries(
                    lambda: self.poly.prices_history(t["token"]),
                    pacer=self.p_pace, venue=CONFIG.venues.POLYMARKET,
                    what=f"history/{t['contract_id']}", conn=self.conn)
            except Refused:
                raise                                # sustained outage: stop the stage
            except VendorError as e:
                store.record_discard(self.conn, "backfill", "no_history",
                                     contract_id=t["contract_id"])
                log.debug("gap: polymarket history %s -> %r", t["contract_id"], e)
                continue
            if not hist:
                # Expected for anything older than the ~30-day retention: a gap
                # is information, recorded, never fabricated.
                store.record_discard(self.conn, "backfill", "history_evaporated",
                                     contract_id=t["contract_id"])
                continue
            self._archive("polymarket", f"history_{t['contract_id']}", hist)
            n_rows += store.upsert_quotes(self.conn, polymarket_history_rows(
                t["contract_id"], hist))
        return {"events": n_events, "contracts": n_contracts, "quote_rows": n_rows,
                "already_captured": skipped, "history_targets": len(targets)}

    # --- driver --------------------------------------------------------------
    def run(self, *, stages=STAGES, scope: str = "neutral",
            limit: int | None = None) -> dict:
        store.init_schema(self.conn)
        summary: dict = {"sport": self.sport.key, "scope": scope,
                         "window_start": self.sport.params.census.window_start,
                         "started_at": store.to_ts(datetime.now(timezone.utc))}
        if "neutral" in stages:
            summary["neutral"] = self.neutral()
        if "kalshi" in stages:
            summary["kalshi"] = self.kalshi_history(scope=scope, limit=limit)
        if "polymarket" in stages:
            summary["polymarket"] = self.polymarket_history(scope=scope, limit=limit)
        summary["finished_at"] = store.to_ts(datetime.now(timezone.utc))
        return summary


# --- payload -> store rows (pure; unit-tested) -------------------------------
def _iso_ts(s: str | None) -> str | None:
    """Vendor ISO string -> our fixed-width UTC encoding, or None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return store.to_ts(dt)


def _unix(s: str | None) -> int | None:
    ts = _iso_ts(s)
    return None if ts is None else int(store.from_ts(ts).timestamp())


def kalshi_candle_rows(ticker: str, candles: list[dict]) -> list[dict]:
    """1-minute candles -> `quotes` rows, source='hist'.

    Order book: mid = (yes_bid.close + yes_ask.close)/2, NEVER de-vigged. A
    candle with no two-sided price is dropped rather than zero-filled.
    """
    rows = []
    for c in candles or []:
        try:
            bid = float(c["yes_bid"]["close_dollars"])
            ask = float(c["yes_ask"]["close_dollars"])
            ts = int(c["end_period_ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if bid <= 0 and ask <= 0:
            continue
        last = (c.get("price") or {}).get("previous_dollars")
        rows.append({
            "contract_id": ticker, "venue": CONFIG.venues.KALSHI,
            "ts": store.to_ts(datetime.fromtimestamp(ts, timezone.utc)),
            "source": "hist", "regime": None,
            "bid": bid, "ask": ask, "mid": round((bid + ask) / 2.0, 6),
            "last": float(last) if last not in (None, "") else None,
            "bid_size_usd": None, "ask_size_usd": None,   # candles carry no depth
        })
    return rows


def polymarket_history_rows(contract_id: str, history: list[dict]) -> list[dict]:
    """`prices-history` points -> `quotes` rows, source='hist'.

    The series is a traded price, so it lands in `last`; bid/ask/depth are
    unknown from history and stay NULL (a gap is a gap). Note the vendor
    returns ~10-minute spacing even at fidelity=1 — coarser than Kalshi's
    1-minute candles, which bounds any lag measured from history.
    """
    rows = []
    for pt in history or []:
        try:
            ts, p = int(pt["t"]), float(pt["p"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({
            "contract_id": contract_id, "venue": CONFIG.venues.POLYMARKET,
            "ts": store.to_ts(datetime.fromtimestamp(ts, timezone.utc)),
            "source": "hist", "regime": None,
            "bid": None, "ask": None, "mid": None, "last": p,
            "bid_size_usd": None, "ask_size_usd": None,
        })
    return rows


def run_backfill(sport, db_path: str | None = None, *, stages=STAGES,
                 scope: str = "neutral", limit: int | None = None,
                 dry_run: bool = False) -> dict:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    conn = store.connect(db_path or sport.params.db_path)
    return Backfill(conn, sport, dry_run=dry_run).run(stages=stages, scope=scope, limit=limit)


if __name__ == "__main__":
    import argparse

    from sports import get_sport

    ap = argparse.ArgumentParser(
        description="one-shot capture of vendor history (no statistic computed)")
    ap.add_argument("--sport", default="cs2")
    ap.add_argument("--db", default=None)
    ap.add_argument("--stages", default=",".join(STAGES),
                    help=f"comma-separated subset of {STAGES}")
    ap.add_argument("--scope", choices=("neutral", "all"), default="neutral",
                    help="which markets get a price series: those joining a "
                         "neutral fixture (default), or every market in the "
                         "window (hours, but nothing is left unrecoverable)")
    ap.add_argument("--limit", type=int, default=None, help="cap series pulls (testing)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be fetched; no network writes")
    a = ap.parse_args()
    out = run_backfill(get_sport(a.sport), a.db, stages=tuple(a.stages.split(",")),
                       scope=a.scope, limit=a.limit, dry_run=a.dry_run)
    print(json.dumps(out, indent=2))
