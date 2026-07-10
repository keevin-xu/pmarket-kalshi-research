"""Fixture builders for tests. Deterministic, tiny."""
from __future__ import annotations

from datetime import datetime, timezone

from db import store


def q(contract_id, dt, *, source="hist", venue="kalshi", bid=None, ask=None,
      mid=None, last=None, regime=None, bid_size=None, ask_size=None):
    return {
        "contract_id": contract_id, "venue": venue, "ts": store.to_ts(dt),
        "source": source, "regime": regime, "bid": bid, "ask": ask,
        "mid": mid, "last": last, "bid_size_usd": bid_size, "ask_size_usd": ask_size,
    }


def dt(y, mo, d, h=0, mi=0, s=0, ms=0):
    return datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=timezone.utc)
