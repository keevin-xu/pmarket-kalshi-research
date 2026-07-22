"""G0 ingest sweep: pull the neutral schedule + both venues' phase-1
contracts into the shapes the coverage/depth census consumes.

- OE  -> tier-1, in-window `matches` (the neutral spine).
- Kalshi (settled KXLOLMAP/KXLOL) -> coverage records + stored contracts.
- Polymarket (closed LoL events) -> coverage records + stored contracts.
- Polymarket (OPEN LoL markets) -> live `/book` snapshots for depth.

All network goes through the adapters (mockable). A non-OK response raises
(VendorError) rather than silently becoming "zero rows". In-window =
start/close >= config.census.window_start. Provenance: hist for settled
coverage rows, live for the depth snapshots.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from config import CONFIG
from census import population as pop
from db import store
from ingest.kalshi import KalshiAdapter, PHASE1_SERIES
from ingest.outcomes import OutcomesAdapter
from ingest.polymarket import PolymarketAdapter

_WINDOW_START = store.from_ts(CONFIG.census.window_start)
_TITLE = re.compile(r"(?:lol|league of legends)\s*:\s*(.+?)\s+vs\.?\s+(.+)",
                    re.IGNORECASE)
_TRAILER = re.compile(r"\s*[\(\-].*$")  # strip "(BO5) - League ..." trailer
_SLUG_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")  # match date lives in the slug


def pm_match_dt(ev: dict) -> datetime | None:
    """True match date for a Polymarket LoL event. `startDate` is the market
    LISTING date (median ~6 days, up to 14, before the match) — using it
    breaks the coverage join. The match date is in the slug
    ('lol-blg-hle1-2026-07-12'); fall back to endDate, then startDate."""
    m = _SLUG_DATE.search(ev.get("slug") or "")
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        except ValueError:
            pass
    return _iso_to_dt(ev.get("endDate")) or _iso_to_dt(ev.get("startDate"))


def _iso_to_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_pm_title(title: str) -> tuple[str, str] | None:
    """'LoL: BLG vs Hanwha Life Esports (BO5) - MSI' -> ('BLG','Hanwha Life
    Esports'). Returns None for non-fixture titles (futures)."""
    m = _TITLE.match(title or "")
    if not m:
        return None
    a = _TRAILER.sub("", m.group(1)).strip()
    b = _TRAILER.sub("", m.group(2)).strip()
    return (a, b) if a and b else None


# --- Oracle's Elixir (neutral spine) -----------------------------------------
def load_oe_matches(paths: list[str]) -> list[dict]:
    """Parse OE CSVs -> tier-1, in-window `matches`. Stored to DB by caller."""
    oa = OutcomesAdapter()
    out: list[dict] = []
    for p in paths:
        rows = oa.to_match_rows(oa.fetch(p))
        for m in rows:
            if m["league"] not in CONFIG.census.tier1_oe_leagues:
                continue
            if store.from_ts(m["start_ts"]) < _WINDOW_START:
                continue
            out.append(m)
    return out


# --- Kalshi (settled map/series winner) --------------------------------------
def sweep_kalshi(adapter: KalshiAdapter | None = None) -> tuple[list[dict], list[dict]]:
    """Return (coverage_records, contract_rows). Pages settled events per
    phase-1 series, stops once out of window (events arrive newest-first)."""
    adapter = adapter or KalshiAdapter()
    records: list[dict] = []
    contracts: list[dict] = []
    for series, family in PHASE1_SERIES.items():
        cursor = None
        stop = False
        while not stop:
            page = adapter.list_events(series, status="settled", cursor=cursor)
            events = page.get("events", [])
            if not events:
                break
            for ev in events:
                mkts = ev.get("markets") or []
                if len(mkts) < 2:
                    continue
                t = _iso_to_dt(mkts[0].get("close_time")) or _iso_to_dt(mkts[0].get("open_time"))
                if t is None:
                    continue
                if t < _WINDOW_START:
                    stop = True
                    continue
                teams = (mkts[0].get("yes_sub_title"), mkts[1].get("yes_sub_title"))
                if not all(teams):
                    continue
                records.append({"teams": teams, "ts": store.to_ts(t),
                                "family": family, "contract_id": mkts[0].get("ticker")})
                for m in mkts:
                    contracts.append({
                        "contract_id": m.get("ticker"), "venue": adapter.venue,
                        "match_id": None, "family": family,
                        "outcome_side": m.get("yes_sub_title"),
                        "question_text": f'{ev.get("title","")}', "parity_ok": None})
            cursor = page.get("cursor") or None
            if not cursor:
                break
    return records, contracts


# --- Polymarket (closed LoL events -> coverage) ------------------------------
def sweep_polymarket(adapter: PolymarketAdapter | None = None) -> tuple[list[dict], list[dict]]:
    adapter = adapter or PolymarketAdapter()
    records: list[dict] = []
    contracts: list[dict] = []
    for ev in adapter.iter_events(closed=True, stop_before=CONFIG.census.window_start):
        pair = parse_pm_title(ev.get("title", ""))
        if pair is None:
            continue
        t = pm_match_dt(ev)  # match date from slug, NOT the listing startDate
        if t is None or t < _WINDOW_START:
            continue
        for m in ev.get("markets", []) or []:
            text = f'{ev.get("title","")} — {m.get("question","")}'
            if pop.is_prop(text):
                continue
            fam = pop.classify_family(text)
            if fam not in CONFIG.census.families_phase1:
                continue
            cid = m.get("conditionId")
            if not cid:  # no market id -> can't key it; a gap, not a fabricated row
                continue
            records.append({"teams": pair, "ts": store.to_ts(t), "family": fam,
                            "contract_id": cid})
            contracts.append({
                "contract_id": m.get("conditionId"), "venue": adapter.venue,
                "match_id": None, "family": fam,
                "outcome_side": f"{pair[0]} vs {pair[1]}",
                "question_text": text, "parity_ok": None})
    return records, contracts


# --- Polymarket (OPEN markets -> live book snapshots for depth) --------------
def sweep_polymarket_live_depth(conn, adapter: PolymarketAdapter | None = None) -> int:
    """Fetch current /book for open LoL phase-1 markets; store live quote
    rows. Depth over settled books is a bug, so depth uses THESE live snaps."""
    adapter = adapter or PolymarketAdapter()
    now = datetime.now(timezone.utc)
    n = 0
    for ev in adapter.iter_events(closed=False):
        pair = parse_pm_title(ev.get("title", ""))
        if pair is None:
            continue
        start = pm_match_dt(ev)
        regime = CONFIG.regimes.PRE_MATCH if (start is None or start > now) \
            else CONFIG.regimes.IN_GAME
        for m in ev.get("markets", []) or []:
            text = f'{ev.get("title","")} — {m.get("question","")}'
            if pop.is_prop(text) or pop.classify_family(text) not in CONFIG.census.families_phase1:
                continue
            import json as _json
            tokens = m.get("clobTokenIds")
            if isinstance(tokens, str):
                try:
                    tokens = _json.loads(tokens)
                except ValueError:
                    tokens = []
            cid = m.get("conditionId")
            if not tokens or not cid:
                continue
            try:
                book = adapter.book(tokens[0])
            except Exception:
                continue
            fam = pop.classify_family(text)
            # quotes FK -> contracts: register the open contract first
            store.upsert_contracts(conn, [{
                "contract_id": cid, "venue": adapter.venue, "match_id": None,
                "family": fam, "outcome_side": f"{pair[0]} vs {pair[1]}",
                "question_text": text, "parity_ok": None}])
            book.update({"_contract_id": cid, "_source": "live",
                         "_regime": regime, "_snapshot_ts": store.to_ts(now),
                         "_last": m.get("lastTradePrice")})
            rows = adapter.to_quote_rows(book)
            n += store.upsert_quotes(conn, rows)
    return n
