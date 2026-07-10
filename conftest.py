"""Pytest fixtures. Mock/replay adapters ONLY — no test hits a live vendor.
Provides an in-memory point-in-time store seeded with tiny fixtures.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from db import store


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def utc():
    def _mk(y, mo, d, h=0, mi=0, s=0, ms=0):
        return datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=timezone.utc)
    return _mk
