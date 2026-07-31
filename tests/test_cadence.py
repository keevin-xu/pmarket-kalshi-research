"""Can a lead measured on mismatched sampling be trusted?

These use synthetic series where the truth is known by construction, because
that is the only way to check a control: on real data you cannot tell an
artifact from a signal, which is the whole problem.
"""
from core.reference import cadence
from core.sport import LeadLagParams

LL = LeadLagParams(divergence_threshold=0.02, confirmation_snapshots=3,
                   convergence_window_s=300, min_divergences=1)


def _drifting(start_t=0, n=120, step_s=60, slope=0.004):
    """A price walking steadily upward — the case where staleness bites."""
    return [(start_t + i * step_s, round(0.30 + i * slope, 6)) for i in range(n)]


def _stepped(start_t=0, n=120, step_s=60, jump=0.10, every=20):
    """A price that JUMPS — the shape a real map produces (a round is won,
    the book reprices). A gentle drift never opens a gap wide enough to count
    as a divergence, so it cannot test lead detection at all."""
    out, price = [], 0.20
    for i in range(n):
        if i and i % every == 0:
            price = round(price + jump, 6)
        out.append((start_t + i * step_s, price))
    return out


def _map(k, p, mid="m1"):
    return {"match_id": mid, "kickoff": k[0][0], "map_end": k[-1][0],
            "kalshi": k, "poly": p}


def _maps(build, n=4):
    """Several independent matches — the block bootstrap needs >= 2 blocks,
    and one match is never a sample."""
    out = []
    for i in range(n):
        k, p = build(i)
        out.append(_map(k, p, mid=f"m{i}"))
    return out


def test_cadence_is_the_median_gap_not_the_mean():
    s = [(0, 0.5), (60, 0.5), (120, 0.5), (7200, 0.5)]   # one huge outage
    assert cadence.cadence_s(s) == 60.0
    assert cadence.cadence_s([(0, 0.5)]) is None


def test_downsample_keeps_only_observed_points():
    s = _drifting(n=20, step_s=60)
    d = cadence.downsample(s, 600)
    assert set(d).issubset(set(s))                 # nothing synthesized
    assert len(d) < len(s)
    assert cadence.cadence_s(d) >= 540             # roughly the coarse grid


def test_control_detects_the_artifact_when_only_cadence_differs():
    """Same venue on both sides — identical information, one side observed
    10x less often. Any lead here is manufactured by sampling alone."""
    def build(i):
        k = _drifting(start_t=i * 100_000, n=120, step_s=60, slope=0.004)
        return k, cadence.downsample(k, 600)

    rep = cadence.compare_cadence(_maps(build), "in_game", ll_params=LL)
    ctrl = rep["artifact_control"]["signed_convergence"]["point"]
    assert rep["artifact_control"]["n_divergences"] > 0
    assert ctrl is not None and ctrl > 0            # the artifact is positive-signed
    assert "artifact" in rep["reading"]


def test_a_genuine_lead_survives_matched_cadence():
    """Kalshi genuinely moves 120s before Polymarket, both observed at 60s.
    Handicapping cadence must not erase a real lead."""
    def build(i):
        k = _stepped(start_t=i * 100_000, n=120, step_s=60)
        lag = 4                                      # 4 steps = 240s behind
        return k, [(t, k[max(0, j - lag)][1]) for j, (t, _) in enumerate(k)]

    rep = cadence.compare_cadence(_maps(build), "in_game", ll_params=LL)
    assert rep["matched"]["signed_convergence"]["point"] > 0
    assert rep["matched"]["leader"] == "kalshi"


def test_report_carries_both_venues_observed_cadence():
    def build(i):
        k = _drifting(start_t=i * 100_000, n=60, step_s=60)
        return k, cadence.downsample(k, 600)

    rep = cadence.compare_cadence(_maps(build), "in_game", ll_params=LL)
    assert rep["cadence_s"]["kalshi_median"] == 60.0
    assert rep["cadence_s"]["polymarket_median"] >= 540


def test_empty_input_says_so_rather_than_implying_no_lead():
    rep = cadence.compare_cadence([], "in_game", ll_params=LL)
    assert rep["raw"]["n_divergences"] == 0
    assert "insufficient" in rep["reading"]
