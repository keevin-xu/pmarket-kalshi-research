"""Shared statistics: seeded event-block bootstrap + scoring rules.

Trades/contracts within one match are NOT independent — resample whole
MATCHES (event blocks), not individual contracts. Everything is seeded so
runs are bit-identical (verify by hashing outputs).
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Callable, Sequence

from config import CONFIG


def brier_score(prices: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean squared error of price (as probability) vs realized 0/1.
    Lower is better. Pure proper scoring rule."""
    if not prices:
        raise ValueError("empty")
    return statistics.fmean((p - y) ** 2 for p, y in zip(prices, outcomes))


def log_loss(prices: Sequence[float], outcomes: Sequence[int], eps: float = 1e-9) -> float:
    def clip(p: float) -> float:
        return min(1 - eps, max(eps, p))
    return -statistics.fmean(
        y * math.log(clip(p)) + (1 - y) * math.log(1 - clip(p))
        for p, y in zip(prices, outcomes)
    )


def reliability_curve(
    prices: Sequence[float], outcomes: Sequence[int], buckets: Sequence[float]
) -> list[dict]:
    """Bucket by price; per bucket return (bucket_center, mean_price,
    realized_rate, n). The empirical heart of calibration."""
    edges = list(buckets)
    out: list[dict] = []
    # assign each price to nearest bucket center
    for center in edges:
        pass
    # simple fixed-width binning around provided centers
    assigned: dict[float, list[int]] = defaultdict(list)
    assigned_p: dict[float, list[float]] = defaultdict(list)
    for p, y in zip(prices, outcomes):
        center = min(edges, key=lambda c: abs(c - p))
        assigned[center].append(y)
        assigned_p[center].append(p)
    for center in edges:
        ys = assigned[center]
        if not ys:
            continue
        out.append({
            "bucket": center,
            "mean_price": statistics.fmean(assigned_p[center]),
            "realized_rate": statistics.fmean(ys),
            "n": len(ys),
        })
    return out


def expected_calibration_error(curve: list[dict], total_n: int) -> float:
    """Sum over buckets of (n_bucket/N) * |mean_price - realized_rate|."""
    if total_n == 0:
        raise ValueError("total_n=0")
    return sum((b["n"] / total_n) * abs(b["mean_price"] - b["realized_rate"]) for b in curve)


def event_block_bootstrap(
    match_ids: Sequence[str],
    values: Sequence[float],
    statistic: Callable[[list[float]], float] = statistics.fmean,
    *,
    resamples: int | None = None,
    seed: int | None = None,
    ci_level: float | None = None,
) -> dict:
    """CI on `statistic` by resampling whole matches with replacement.

    All values sharing a match_id move together as one block. Seeded and
    deterministic. Returns point estimate, CI, n_blocks.
    """
    resamples = resamples or CONFIG.bootstrap.resamples
    seed = CONFIG.bootstrap.seed if seed is None else seed
    ci_level = ci_level or CONFIG.lead_lag.ci_level

    blocks: dict[str, list[float]] = defaultdict(list)
    for m, v in zip(match_ids, values):
        blocks[m].append(v)
    keys = sorted(blocks)
    if len(keys) < 2:
        return {"point": None, "ci_lo": None, "ci_hi": None, "n_blocks": len(keys)}

    flat = [v for k in keys for v in blocks[k]]
    point = statistic(flat)

    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        for _ in keys:
            sample.extend(blocks[rng.choice(keys)])
        stats.append(statistic(sample))
    stats.sort()
    lo_i = int((1 - ci_level) / 2 * len(stats))
    hi_i = int((1 + ci_level) / 2 * len(stats)) - 1
    return {
        "point": point,
        "ci_lo": stats[lo_i],
        "ci_hi": stats[hi_i],
        "n_blocks": len(keys),
    }
