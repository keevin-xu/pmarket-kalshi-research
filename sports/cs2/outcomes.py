"""CS2 neutral ground truth — bo3.gg.

THE ANSWER KEY, and the neutral schedule coverage is checked against. Never a
venue's settlement: recon found the two venues resolve an uncompleted map
differently (Polymarket 50-50, Kalshi "fair market price"), so only an
independent source can arbitrate.

Two properties of this vendor shape the design (DECISIONS.md [2026-07-30]
NOTE — bo3.gg recon):

  * **Unsupported filters are ignored, not rejected.** `filter[matches.tier]
    [eq]=s` binds; `filter[status]=…`, `[gte]`, `where[…]` return the FULL
    unfiltered set with HTTP 200. So every filtered pull asserts that the
    filter actually bound (`_assert_filter_bound`) — an unverified sweep here
    would silently widen the population to all 78k matches.
  * **Rounds carry no wall-clock.** `game_rounds.created_at` is a bulk
    parse-time insert hours after play, so halftime is not timestampable and
    the in-game checkpoint is `games.begin_at + in_game_checkpoint_s`.

Network pulls are ARCHIVED VERBATIM and everything downstream reads the
archive, never the vendor: Kalshi drops market rows after ~68 days and
Polymarket history after ~30, so a re-fetch can only return LESS than the
run before it. `pull_window()` is the only method that touches the network;
`fetch()` reads an archived snapshot from disk.

Units pinned on first pull: `duration` and `round_duration` are NANOSECONDS;
timestamps are ISO-8601 with a `+00:00` offset.
"""
from __future__ import annotations

import glob
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from core.census import coverage as cov
from core.db import store
from core.ingest.base import HTTP_HEADERS, SSL_CONTEXT, Adapter, VendorError
from sports.cs2 import params as P

NS_PER_S = 1_000_000_000
NEUTRAL_SOURCE = "bo3.gg"
ARCHIVE_SUBDIR = "bo3"


def _iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _ns_to_s(v) -> int | None:
    try:
        return int(float(v) / NS_PER_S)
    except (TypeError, ValueError):
        return None


class FilterIgnored(VendorError):
    """A filter came back unbound — the response is the whole table, not the
    requested slice. Loud by design: this is the failure mode that silently
    swaps a tier-1 population for every CS2 match ever played."""


class OutcomesAdapter(Adapter):
    venue = "neutral"

    def __init__(self, base: str | None = None):
        self.base = (base or P.BO3_API_BASE).rstrip("/")
        self._last_request = 0.0

    # --- local read (the seam every gate uses; no network) -------------------
    def fetch(self, path: str) -> dict:
        """Read one archived snapshot. A missing file raises, never returns {}."""
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def to_quote_rows(self, payload):     # outcomes are not quotes
        raise TypeError("outcomes are not quotes; use to_match_rows/to_map_results")

    # --- network (backfill only) --------------------------------------------
    def _get(self, resource: str, params: dict) -> dict:
        """One paced GET. Non-OK raises VendorError — never 'zero rows'.

        bo3.gg publishes no rate-limit or Retry-After header, so the interval
        is self-imposed; quota is treated as production.
        """
        wait = P.BO3_MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        qs = urllib.parse.urlencode(params, safe="[]")
        url = f"{self.base}/{resource.lstrip('/')}" + (f"?{qs}" if qs else "")
        req = urllib.request.Request(url, headers=HTTP_HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as resp:
                if resp.status != 200:
                    raise VendorError(f"bo3 {url} -> HTTP {resp.status}", status=resp.status)
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise VendorError(f"bo3 {url} -> HTTP {e.code}", status=e.code) from e
        except urllib.error.URLError as e:
            raise VendorError(f"bo3 {url} -> {e.reason}", status=0) from e

    @staticmethod
    def _total(page: dict) -> int:
        return int(((page or {}).get("total") or {}).get("count") or 0)

    def _assert_filter_bound(self, resource: str, filters: dict) -> int:
        """Prove the filter narrowed the resource before trusting a sweep.

        Returns the filtered total. Raises FilterIgnored if it equals the
        unfiltered total, which is exactly what this API returns for a filter
        expression it does not understand.
        """
        unfiltered = self._total(self._get(resource, {"page[limit]": 1}))
        filtered = self._total(self._get(resource, {"page[limit]": 1, **filters}))
        if unfiltered and filtered == unfiltered:
            raise FilterIgnored(
                f"bo3 {resource}: filters {filters} did not bind "
                f"(total unchanged at {unfiltered}) — refusing to sweep an "
                f"unfiltered population")
        return filtered

    def _paged(self, resource: str, filters: dict, *, sort: str,
               expected_total: int | None = None) -> list[dict]:
        """Offset-paginate a filtered resource. Deep offsets are reachable
        (verified at recon), so a page cap cannot hide late rows."""
        rows: list[dict] = []
        offset = 0
        while True:
            page = self._get(resource, {"page[limit]": P.BO3_PAGE_LIMIT,
                                        "page[offset]": offset, "sort": sort, **filters})
            batch = page.get("results") or []
            rows += batch
            offset += len(batch)
            if not batch or offset >= self._total(page):
                break
        if expected_total is not None and len(rows) != expected_total:
            raise VendorError(f"bo3 {resource}: swept {len(rows)} rows, vendor "
                              f"reported {expected_total} — incomplete sweep")
        return rows

    def pull_window(self, *, window_start: str, window_end: str | None = None,
                    tiers=("s", "a"), status: str = "finished",
                    archive_dir: str | None = None) -> str:
        """Pull one window of the neutral schedule + maps and ARCHIVE it.

        Returns the archive path. Timestamps are the vendor's own strings; we
        keep raw rows verbatim and normalize only at parse time, so a schema
        change can be re-parsed from the archive rather than re-fetched.
        """
        matches: list[dict] = []
        for tier in tiers:
            f = {"filter[matches.start_date][gt]": window_start,
                 "filter[matches.status][eq]": status,
                 "filter[matches.tier][eq]": tier}
            if window_end:
                f["filter[matches.start_date][lt]"] = window_end
            total = self._assert_filter_bound("matches", f)
            matches += self._paged("matches", f, sort="start_date", expected_total=total)

        match_ids = [m["id"] for m in matches if m.get("id") is not None]
        games = self._batched("games", "filter[games.match_id][in]", match_ids)
        team_ids = sorted({t for m in matches
                           for t in (m.get("team1_id"), m.get("team2_id")) if t})
        teams = self._batched("teams", "filter[teams.id][in]", team_ids)
        tour_ids = sorted({m["tournament_id"] for m in matches if m.get("tournament_id")})
        tours = self._batched("tournaments", "filter[tournaments.id][in]", tour_ids)

        snapshot = {
            "_source": NEUTRAL_SOURCE,
            "_pulled_at": store.to_ts(datetime.now(timezone.utc)),
            "_window": {"start": window_start, "end": window_end},
            "_filters": {"tiers": list(tiers), "status": status},
            "_filters_bound": True,
            "matches": matches,
            "games": games,
            "teams": {str(t["id"]): t for t in teams if t.get("id") is not None},
            "tournaments": {str(t["id"]): t for t in tours if t.get("id") is not None},
        }
        out = Path(archive_dir or ".") / ARCHIVE_SUBDIR
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"window_{snapshot['_pulled_at'].replace(':', '').replace('.', '')}.json"
        path.write_text(json.dumps(snapshot, indent=1))
        return str(path)

    def fixtures_since(self, since_iso: str) -> list[dict]:
        """Live/upcoming/just-finished fixtures with their NEUTRAL tier.

        Needed because depth is measured on OPEN markets, which by definition
        are not in the settled archive. Tier still comes from the neutral row —
        never from a venue title — so the depth measurement covers the same
        population the gate names.
        """
        f = {"filter[matches.start_date][gt]": since_iso}
        self._assert_filter_bound("matches", f)
        matches = self._paged("matches", f, sort="start_date")
        team_ids = sorted({t for m in matches
                           for t in (m.get("team1_id"), m.get("team2_id")) if t})
        teams = {str(t["id"]): t for t in self._batched("teams", "filter[teams.id][in]", team_ids)
                 if t.get("id") is not None}
        out = []
        for m in matches:
            a, b = self._team_name(teams, m.get("team1_id")), self._team_name(teams, m.get("team2_id"))
            start = _iso(m.get("start_date"))
            if not (a and b and start):
                continue
            out.append({"teams": (a, b), "ts": store.to_ts(start),
                        "tier": (m.get("tier") or "").strip().lower() or None,
                        "status": m.get("status")})
        return out

    def _batched(self, resource: str, filter_key: str, ids: list, size: int = 40) -> list[dict]:
        """Fetch rows by id in batches, paginating WITHIN each batch.

        `page[limit]` is capped server-side (observed: 100 returned for a
        requested 200), and one match can have several maps, so a batch of ids
        routinely exceeds one page. The completeness check below is what
        caught that cap — a short page here would silently drop maps.
        """
        out: list[dict] = []
        for i in range(0, len(ids), size):
            chunk = ",".join(str(x) for x in ids[i:i + size])
            offset, total = 0, None
            while True:
                page = self._get(resource, {"page[limit]": P.BO3_PAGE_LIMIT,
                                            "page[offset]": offset, filter_key: chunk})
                rows = page.get("results") or []
                total = self._total(page) if total is None else total
                out += rows
                offset += len(rows)
                if not rows or offset >= total:
                    break
            if total and offset < total:
                raise VendorError(f"bo3 {resource}: id batch incomplete "
                                  f"({offset} of {total})")
        return out

    # --- normalization -------------------------------------------------------
    @staticmethod
    def merge(snapshots: list[dict]) -> dict:
        """Merge archived snapshots; the newest pull wins per id. Idempotent —
        re-reading the same archive twice yields the same merged view."""
        ordered = sorted(snapshots, key=lambda s: s.get("_pulled_at") or "")
        matches: dict[str, dict] = {}
        games: dict[str, dict] = {}
        teams: dict[str, dict] = {}
        tours: dict[str, dict] = {}
        for s in ordered:
            for m in s.get("matches") or []:
                matches[str(m.get("id"))] = m
            for g in s.get("games") or []:
                games[str(g.get("id"))] = g
            teams.update(s.get("teams") or {})
            tours.update(s.get("tournaments") or {})
        return {"matches": list(matches.values()), "games": list(games.values()),
                "teams": teams, "tournaments": tours}

    @staticmethod
    def _team_name(teams: dict, team_id) -> str | None:
        t = teams.get(str(team_id)) or {}
        return t.get("name") or t.get("slug")

    def to_match_rows(self, snapshot: dict) -> list[dict]:
        """Neutral spine, one row per MATCH (series). `league` carries the
        tournament name — CS2 has tournaments, not leagues. A match missing a
        team name, a start time or (when finished) a winner is skipped: a gap
        is information, never a fabricated row."""
        teams = snapshot.get("teams") or {}
        tours = snapshot.get("tournaments") or {}
        out: list[dict] = []
        for m in snapshot.get("matches") or []:
            a = self._team_name(teams, m.get("team1_id"))
            b = self._team_name(teams, m.get("team2_id"))
            start = _iso(m.get("start_date"))
            if not (a and b and start):
                continue
            end = _iso(m.get("end_date")) or start
            tour = tours.get(str(m.get("tournament_id"))) or {}
            out.append({
                "match_id": f"bo3:{m.get('id')}",
                "league": tour.get("name") or "",
                "team_a": a,
                "team_b": b,
                "start_ts": store.to_ts(start),
                "best_of": m.get("bo_type"),
                "neutral_source": NEUTRAL_SOURCE,
                "result_winner": self._team_name(teams, m.get("winner_team_id")),
                "result_ts": store.to_ts(end),
                "_tier": (m.get("tier") or "").strip().lower() or None,
                "_status": m.get("status"),
                "_bo3_match_id": m.get("id"),
            })
        return out

    @staticmethod
    def _resolve_side(clan_name: str | None, names: tuple[str, str]) -> str | None:
        """Map a game's clan name onto one of the match's two team names.

        bo3 reports map winners as in-game clan names ("Lifes A Game") while
        the team row holds the short name ("LAG"), so this is a fuzzy match
        that must be UNAMBIGUOUS: matching both sides, or neither, returns
        None and the map is left without an outcome rather than guessed.

        An EXACT normalized hit wins outright, because the shared fuzzy
        matcher treats two single-token names sharing a first letter as the
        same team (MOUZ/MIBR, BIG/B8) — without this, a map whose winner is
        named exactly would be discarded as ambiguous.
        """
        if not clan_name:
            return None
        exact = [n for n in names
                 if n and cov.normalize_team(clan_name) == cov.normalize_team(n)]
        if len(exact) == 1:
            return exact[0]
        hits = [n for n in names if n and cov.team_match(clan_name, n)]
        return hits[0] if len(hits) == 1 else None

    def to_map_results(self, snapshot: dict) -> list[dict]:
        """Per-MAP records: {teams, ts (map start), map_no, winner, match_id,
        gamelen_s}. `ts` is `games.begin_at` — the anchor for both the
        pre-match snapshot and the in-game checkpoint."""
        by_match = {m["_bo3_match_id"]: m for m in self.to_match_rows(snapshot)}
        out: list[dict] = []
        for g in snapshot.get("games") or []:
            m = by_match.get(g.get("match_id"))
            begin = _iso(g.get("begin_at"))
            if not m or begin is None or g.get("number") is None:
                continue
            names = (m["team_a"], m["team_b"])
            out.append({
                "teams": names,
                "ts": store.to_ts(begin),
                "map_no": int(g["number"]),
                "winner": self._resolve_side(g.get("winner_clan_name"), names),
                "match_id": f"{m['match_id']}:m{g['number']}",
                "gamelen_s": self.map_length_s(g),
                "_tier": m["_tier"],
                "_league": m["league"],
                "_best_of": m["best_of"],
                "_map_name": g.get("map_name"),
                "_rounds_count": g.get("rounds_count"),
                "_state": g.get("state"),
            })
        return out

    @staticmethod
    def map_length_s(game: dict) -> int | None:
        """Map wall-clock length in seconds, or None when the vendor's number
        cannot be trusted.

        `duration` is nanoseconds, and recon found rows reporting 1-3 minutes
        against 14+ played rounds (aborted / mis-parsed maps). A duration
        below `MIN_SECONDS_PER_ROUND` per played round is treated as unknown,
        so such a map can never satisfy the in-game checkpoint's
        "still in progress" test by accident.
        """
        secs = _ns_to_s(game.get("duration"))
        if secs is None or secs <= 0:
            return None
        rounds = game.get("rounds_count")
        try:
            rounds = int(rounds)
        except (TypeError, ValueError):
            rounds = None
        if rounds and secs < rounds * P.MIN_SECONDS_PER_ROUND:
            return None
        return secs


def checkpoint_status(map_record: dict, checkpoint_s: int) -> str:
    """Is this map evaluable at the in-game checkpoint? Returns "ok" or a
    DISCARD REASON CODE (never a silent skip).

    Part of the frozen rule, not an implementation detail: a checkpoint that
    landed after a map had resolved would read the outcome back as a price,
    and it would do so selectively on fast, lopsided maps — biasing
    calibration exactly where prices are extreme. Discards are reported by
    tier and map number alongside the G2 result.
    """
    if not map_record.get("ts"):
        return "no_map_start"
    gamelen = map_record.get("gamelen_s")
    if not gamelen:
        return "unreliable_map_length"
    if gamelen < checkpoint_s:
        return "checkpoint_after_map_end"
    return "ok"


def archive_paths(raw_dir: str) -> list[str]:
    return sorted(glob.glob(str(Path(raw_dir) / ARCHIVE_SUBDIR / "window_*.json")))
