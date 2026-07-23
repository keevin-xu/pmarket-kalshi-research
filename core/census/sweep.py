"""Ingest sweep: pull both venues' phase-1 contracts into the shapes the
coverage / parity / calibration / lead-lag stages consume. SPORT-AGNOSTIC —
discovery, classification, families and window all come from the `sport`
handed in; no venue/league string is hard-coded here.

The neutral schedule/outcomes themselves come from `sport.load_matches()` /
`sport.load_map_results()` (each sport's own source). A non-OK vendor response
raises (VendorError), never "zero rows". In-window = ts >= sport window_start.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from core.config import CONFIG
from core.db import store
from core.ingest.kalshi import KalshiAdapter
from core.ingest.polymarket import PolymarketAdapter

# Generic "<Sport>: TeamA vs TeamB (…) - League" title -> (TeamA, TeamB).
_TITLE = re.compile(r"^[^:]+:\s*(.+?)\s+vs\.?\s+(.+)", re.IGNORECASE)
_TRAILER = re.compile(r"\s*[\(\-].*$")
_SLUG_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_GAME_NO = re.compile(r"\bgame (\d+)\b", re.IGNORECASE)


def _iso_to_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _window_start(sport) -> datetime:
    return store.from_ts(sport.params.census.window_start)


def pm_match_dt(ev: dict) -> datetime | None:
    """True match date from the Polymarket event SLUG (its `startDate` is the
    LISTING date, days early, and breaks the join); fall back endDate/startDate."""
    m = _SLUG_DATE.search(ev.get("slug") or "")
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        except ValueError:
            pass
    return _iso_to_dt(ev.get("endDate")) or _iso_to_dt(ev.get("startDate"))


def parse_pm_title(title: str) -> tuple[str, str] | None:
    """'<Sport>: A vs B (BO5) - League' -> ('A','B'); None for non-fixtures."""
    m = _TITLE.match(title or "")
    if not m:
        return None
    a = _TRAILER.sub("", m.group(1)).strip()
    b = _TRAILER.sub("", m.group(2)).strip()
    return (a, b) if a and b else None


def _pm_winner(market: dict) -> str | None:
    """Winning outcome of a settled Polymarket market, or None (void). outcomes
    / outcomePrices are JSON-string arrays aligned by index."""
    outs, prices = market.get("outcomes"), market.get("outcomePrices")
    if isinstance(outs, str):
        try:
            outs = json.loads(outs)
        except ValueError:
            return None
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            return None
    if not outs or not prices or len(outs) != len(prices):
        return None
    for name, pr in zip(outs, prices):
        try:
            if abs(float(pr) - 1.0) < 1e-9:
                return name
        except (TypeError, ValueError):
            continue
    return None


def _json_list(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return []
    return v or []


# --- Kalshi (settled map/series winner) --------------------------------------
def sweep_kalshi(sport, adapter: KalshiAdapter | None = None):
    """(coverage_records, contract_rows) across the sport's phase-1 series."""
    adapter = adapter or KalshiAdapter()
    win = _window_start(sport)
    records, contracts = [], []
    for series, family in sport.kalshi_series().items():
        cursor, stop = None, False
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
                if t < win:
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


# --- Polymarket (closed events -> coverage) ----------------------------------
def sweep_polymarket(sport, adapter: PolymarketAdapter | None = None):
    adapter = adapter or PolymarketAdapter()
    win = _window_start(sport)
    families = sport.params.census.families_phase1
    records, contracts = [], []
    for ev in adapter.iter_events(sport.polymarket_tag(), closed=True,
                                  stop_before=sport.params.census.window_start):
        pair = parse_pm_title(ev.get("title", ""))
        if pair is None:
            continue
        t = pm_match_dt(ev)
        if t is None or t < win:
            continue
        for m in ev.get("markets", []) or []:
            text = f'{ev.get("title","")} — {m.get("question","")}'
            if sport.is_prop(text):
                continue
            fam = sport.classify_family(text)
            if fam not in families:
                continue
            cid = m.get("conditionId")
            if not cid:
                continue
            records.append({"teams": pair, "ts": store.to_ts(t), "family": fam,
                            "contract_id": cid})
            contracts.append({
                "contract_id": cid, "venue": adapter.venue, "match_id": None,
                "family": fam, "outcome_side": f"{pair[0]} vs {pair[1]}",
                "question_text": text, "parity_ok": None})
    return records, contracts


# --- Per-map SETTLED results (parity / calibration / lead-lag) ----------------
def sweep_kalshi_map_results(sport, adapter: KalshiAdapter | None = None) -> list[dict]:
    adapter = adapter or KalshiAdapter()
    win = _window_start(sport)
    map_series = [t for t, f in sport.kalshi_series().items() if f == "map_winner"]
    out: list[dict] = []
    for series in map_series:
        cursor = None
        while True:
            page = adapter.list_events(series, status="settled", cursor=cursor)
            events = page.get("events", [])
            if not events:
                break
            for ev in events:
                mkts = ev.get("markets") or []
                if len(mkts) < 2:
                    continue
                tail = (ev.get("event_ticker") or "").rsplit("-", 1)[-1]
                if not tail.isdigit():
                    continue
                t = _iso_to_dt(mkts[0].get("close_time"))
                if t is None or t < win:
                    continue
                teams = (mkts[0].get("yes_sub_title"), mkts[1].get("yes_sub_title"))
                if not all(teams):
                    continue
                winner = next((m.get("yes_sub_title") for m in mkts
                               if str(m.get("result", "")).strip() == "yes"), None)
                out.append({"teams": teams, "ts": store.to_ts(t), "map_no": int(tail),
                            "winner": winner, "contract_id": ev.get("event_ticker"),
                            "series": series,
                            "team_markets": {m.get("yes_sub_title"): m.get("ticker")
                                             for m in mkts}})
            cursor = page.get("cursor") or None
            if not cursor:
                break
    return out


def sweep_polymarket_map_results(sport, adapter: PolymarketAdapter | None = None) -> list[dict]:
    adapter = adapter or PolymarketAdapter()
    win = _window_start(sport)
    out: list[dict] = []
    for ev in adapter.iter_events(sport.polymarket_tag(), closed=True,
                                  stop_before=sport.params.census.window_start):
        pair = parse_pm_title(ev.get("title", ""))
        if pair is None:
            continue
        t = pm_match_dt(ev)
        if t is None or t < win:
            continue
        for m in ev.get("markets", []) or []:
            text = f'{ev.get("title","")} — {m.get("question","")}'
            if sport.is_prop(text) or sport.classify_family(text) != "map_winner":
                continue
            g = _GAME_NO.search(m.get("question", ""))
            if not g:
                continue
            out.append({"teams": pair, "ts": store.to_ts(t), "map_no": int(g.group(1)),
                        "winner": _pm_winner(m), "contract_id": m.get("conditionId"),
                        "outcomes": _json_list(m.get("outcomes")),
                        "tokens": _json_list(m.get("clobTokenIds"))})
    return out


# --- Polymarket (OPEN markets -> live book snapshots for depth) --------------
def sweep_polymarket_live_depth(sport, conn, adapter: PolymarketAdapter | None = None) -> int:
    adapter = adapter or PolymarketAdapter()
    families = sport.params.census.families_phase1
    now = datetime.now(timezone.utc)
    n = 0
    for ev in adapter.iter_events(sport.polymarket_tag(), closed=False):
        pair = parse_pm_title(ev.get("title", ""))
        if pair is None:
            continue
        start = pm_match_dt(ev)
        regime = CONFIG.regimes.PRE_MATCH if (start is None or start > now) \
            else CONFIG.regimes.IN_GAME
        for m in ev.get("markets", []) or []:
            text = f'{ev.get("title","")} — {m.get("question","")}'
            fam = sport.classify_family(text)
            if sport.is_prop(text) or fam not in families:
                continue
            tokens = _json_list(m.get("clobTokenIds"))
            cid = m.get("conditionId")
            if not tokens or not cid:
                continue
            try:
                book = adapter.book(tokens[0])
            except Exception:
                continue
            store.upsert_contracts(conn, [{
                "contract_id": cid, "venue": adapter.venue, "match_id": None,
                "family": fam, "outcome_side": f"{pair[0]} vs {pair[1]}",
                "question_text": text, "parity_ok": None}])
            book.update({"_contract_id": cid, "_source": "live", "_regime": regime,
                         "_snapshot_ts": store.to_ts(now),
                         "_last": m.get("lastTradePrice")})
            n += store.upsert_quotes(conn, adapter.to_quote_rows(book))
    return n
