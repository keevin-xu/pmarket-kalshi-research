import statistics

from analysis import metrics


def test_brier_and_ece_perfect_calibration():
    # prices exactly equal realized rates within buckets -> ECE ~ 0
    prices = [0.5, 0.5, 0.5, 0.5]
    outcomes = [1, 0, 1, 0]
    curve = metrics.reliability_curve(prices, outcomes, (0.5,))
    assert curve[0]["realized_rate"] == 0.5
    assert metrics.expected_calibration_error(curve, 4) == 0.0


def test_bootstrap_is_deterministic():
    mids = ["m1", "m1", "m2", "m3", "m3"]
    vals = [0.1, 0.2, 0.3, 0.4, 0.5]
    a = metrics.event_block_bootstrap(mids, vals, resamples=500, seed=1)
    b = metrics.event_block_bootstrap(mids, vals, resamples=500, seed=1)
    assert a == b                     # bit-identical for same seed
    assert a["n_blocks"] == 3


def test_bootstrap_blocks_keep_matches_together():
    # single block -> cannot form a CI
    out = metrics.event_block_bootstrap(["m1", "m1"], [0.1, 0.2])
    assert out["n_blocks"] == 1 and out["ci_lo"] is None
