"""Tests for the max-flow / min-cut payment-feasibility sampler.

The anchor is the worked example from Pickhardt's notebook, where the answer is known
exactly by enumeration, so the sampler is checked against ground truth rather than
against itself.
"""

import numpy as np
import pytest

from lnhistoryclient.analysis.mincut import (
    amount_at_service_level,
    channel_table,
    exact_feasibility,
    feasibility,
    feasibility_curve,
    induced_subgraph,
    professional_nodes,
    sample_min_cuts,
    survival_curve,
)

# The example network from the original notebook:
#
#                 (C)
#                /   \
#               2     2
#              /       \
#   (S)--10--(A)---2---(B)--8--(R)
#
# S and R are pendants with plenty of capacity, so the S-R min-cut is decided by the
# A-B triangle. With a_b, a_c, c_b all of capacity 2 there are 3*3*3 = 27 liquidity
# states of the triangle, and the max-flow from A to B is a_b + min(a_c, c_b).
TRIANGLE = [
    ("S", "A", 10),
    ("A", "B", 2),
    ("A", "C", 2),
    ("C", "B", 2),
    ("B", "R", 8),
]
TRIANGLE_CORE = [("A", "B", 2), ("A", "C", 2), ("C", "B", 2)]


def _enumerate_triangle(amount: int) -> float:
    """Ground truth for the A->B triangle, straight from the notebook's own loop."""
    feasible = 0
    for a_b in range(3):
        for a_c in range(3):
            for c_b in range(3):
                if a_b + min(a_c, c_b) >= amount:
                    feasible += 1
    return feasible / 27


@pytest.mark.parametrize("amount,expected", [(0, 27 / 27), (1, 22 / 27), (2, 14 / 27), (3, 5 / 27), (4, 1 / 27)])
def test_exact_matches_hand_enumeration(amount, expected):
    """exact_feasibility on the triangle reproduces the notebook's published table."""
    assert exact_feasibility(TRIANGLE_CORE, "A", "B", amount) == pytest.approx(expected)


def test_published_two_sat_probability():
    """The notebook's headline for this example: a 2 sat payment is possible 51.85%."""
    assert exact_feasibility(TRIANGLE_CORE, "A", "B", 2) == pytest.approx(0.5185, abs=5e-4)
    assert _enumerate_triangle(2) == pytest.approx(0.5185, abs=5e-4)


def test_max_flow_is_not_the_most_likely_flow():
    """P(a 2 sat payment is possible) exceeds the 1/3 success of the best single flow.

    This is the observation the whole study rests on: routing one optimal flow
    understates what the network could deliver if the sender knew every balance.
    """
    assert exact_feasibility(TRIANGLE_CORE, "A", "B", 2) > 1 / 3


def test_sampler_converges_to_exact():
    """Sampled feasibility converges on the enumerated truth for the same graph.

    The core triangle is K3 with equal capacities, so it is symmetric under relabelling
    and every ordered pair has the same min-cut distribution. That makes the sampler's
    pair-averaged result directly comparable to the single-pair enumeration.
    """
    samples = sample_min_cuts(TRIANGLE_CORE, trials=20_000, seed=7)
    assert samples.values.min() >= 0
    assert samples.values.max() <= 4  # a_b + min(a_c, c_b) <= 2 + 2
    for amount in (1, 2, 3, 4):
        expected = exact_feasibility(TRIANGLE_CORE, "A", "B", amount)
        assert feasibility(samples.values, amount) == pytest.approx(expected, abs=0.015)


def test_sampler_matches_exact_for_a_fixed_pair():
    """With only two nodes there is one pair, so sampling must match enumeration."""
    two_node = [("A", "B", 6)]
    samples = sample_min_cuts(two_node, trials=20_000, seed=3)
    # capacity 6 split uniformly: P(flow >= 3) over both directions = P(r >= 3) = 4/7
    assert feasibility(samples.values, 3) == pytest.approx(4 / 7, abs=0.02)
    assert feasibility(samples.values, 0) == 1.0
    assert feasibility(samples.values, 7) == 0.0


def test_samples_summary_fields():
    samples = sample_min_cuts(TRIANGLE, trials=200, seed=1, label="toy", scope="full")
    assert samples.nodes == 5
    assert samples.channels == 5
    assert samples.total_capacity_sat == 10 + 2 + 2 + 2 + 8
    assert samples.trials == len(samples.values) == 200
    assert samples.avg_degree == pytest.approx(2 * 5 / 5)
    assert samples.label == "toy" and samples.scope == "full"


def test_round_trip_serialisation():
    samples = sample_min_cuts(TRIANGLE, trials=50, seed=2, label="toy", scope="full")
    from lnhistoryclient.analysis.mincut import MinCutSamples

    restored = MinCutSamples.from_dict(samples.to_dict())
    assert np.array_equal(restored.values, samples.values)
    assert restored.total_capacity_sat == samples.total_capacity_sat
    assert restored.label == "toy"


def test_seed_is_reproducible():
    a = sample_min_cuts(TRIANGLE, trials=300, seed=42)
    b = sample_min_cuts(TRIANGLE, trials=300, seed=42)
    c = sample_min_cuts(TRIANGLE, trials=300, seed=43)
    assert np.array_equal(a.values, b.values)
    assert not np.array_equal(a.values, c.values)


def test_feasibility_is_monotone_and_bounded():
    values = np.array([0, 10, 10, 50, 100, 1000])
    curve = feasibility_curve(values, [0, 10, 50, 100, 1000, 10_000])
    assert list(curve) == [
        1.0,
        pytest.approx(5 / 6),
        pytest.approx(3 / 6),
        pytest.approx(2 / 6),
        pytest.approx(1 / 6),
        0.0,
    ]
    assert all(curve[i] >= curve[i + 1] for i in range(len(curve) - 1))


def test_feasibility_curve_matches_scalar():
    rng = np.random.default_rng(0)
    values = rng.integers(0, 1_000_000, size=500)
    amounts = [0, 1, 1000, 50_000, 999_999, 2_000_000]
    curve = feasibility_curve(values, amounts)
    for amount, got in zip(amounts, curve, strict=False):
        assert got == pytest.approx(feasibility(values, amount))


def test_survival_curve_starts_at_one():
    values = np.array([0, 5, 5, 9])
    x, y = survival_curve(values)
    assert list(x) == [0, 5, 9]
    assert y[0] == 1.0
    assert list(y) == [1.0, pytest.approx(3 / 4), pytest.approx(1 / 4)]


def test_service_level_inversion_agrees_with_feasibility():
    """The amount returned at level p must actually be feasible in >= p of samples."""
    rng = np.random.default_rng(11)
    values = rng.integers(0, 10_000_000, size=5000)
    for probability in (0.999, 0.99, 0.975, 0.9, 0.5):
        amount = amount_at_service_level(values, probability)
        assert feasibility(values, amount) >= probability


def test_service_level_is_monotone():
    rng = np.random.default_rng(5)
    values = rng.integers(0, 1_000_000, size=2000)
    levels = [0.999, 0.99, 0.9, 0.5]
    amounts = [amount_at_service_level(values, p) for p in levels]
    assert amounts == sorted(amounts)  # a weaker guarantee permits a larger amount


def test_induced_subgraph_keeps_only_internal_channels():
    kept = induced_subgraph(TRIANGLE, {"A", "B", "C"})
    assert sorted(kept) == sorted(TRIANGLE_CORE)
    assert induced_subgraph(TRIANGLE, {"A"}) == []


def test_professional_nodes_filters_and_intersects():
    scores = {"a": 100, "b": 0, "c": 42}
    assert professional_nodes(scores) == ["a", "c"]
    assert professional_nodes(scores, present={"c", "z"}) == ["c"]


def test_disconnected_pairs_yield_zero():
    """Two components: any cross-component pair has a min-cut of 0."""
    split = [("A", "B", 100), ("C", "D", 100)]
    samples = sample_min_cuts(split, trials=2000, seed=4)
    # 8 of 12 ordered pairs cross the components -> about two thirds are exactly 0
    assert feasibility(samples.values, 1) == pytest.approx(1 / 3, abs=0.05)


def test_rejects_empty_or_degenerate_input():
    with pytest.raises(ValueError, match="no channels"):
        sample_min_cuts([], trials=10)
    with pytest.raises(ValueError, match="trials must be positive"):
        sample_min_cuts(TRIANGLE, trials=0)
    with pytest.raises(ValueError, match="probability must be in"):
        amount_at_service_level(np.array([1, 2]), 0.0)
    with pytest.raises(ValueError, match="too many to enumerate"):
        exact_feasibility([("A", "B", 10_000_000)], "A", "B", 1)


def test_networkx_fallback_agrees_with_igraph():
    """The no-igraph path must give identical answers, not merely similar ones.

    igraph is normally installed, so this branch would otherwise never run — and an
    untested fallback is exactly where an edge-indexing bug would survive.
    """
    from lnhistoryclient.analysis import mincut

    rng = np.random.default_rng(19)
    channels = [
        ("A", "B", 40),
        ("B", "C", 15),
        ("A", "C", 25),
        ("C", "D", 30),
        ("B", "D", 5),
        ("D", "E", 60),
        ("E", "F", 12),  # F is a pendant
        ("G", "H", 20),  # a second component
    ]
    graph = mincut._FlowGraph(channels)
    n = graph.n
    for _ in range(60):
        state = graph.draw_state(rng)
        source, target = (int(v) for v in rng.choice(n, size=2, replace=False))
        igraph_value = graph.max_flow(state, source, target)
        networkx_value = graph._max_flow_networkx(state, source, target)
        assert igraph_value == networkx_value, f"{source}->{target}: {igraph_value} != {networkx_value}"


def test_fallback_used_when_igraph_missing(monkeypatch):
    """With igraph disabled, sampling still runs and still matches enumeration."""
    from lnhistoryclient.analysis import mincut

    monkeypatch.setattr(mincut, "HAVE_IGRAPH", False)
    samples = mincut.sample_min_cuts(TRIANGLE_CORE, trials=3000, seed=13)
    for amount in (1, 2, 3):
        expected = _enumerate_triangle(amount)
        assert feasibility(samples.values, amount) == pytest.approx(expected, abs=0.035)


def test_channel_table_merges_parallels_and_drops_unknown_capacity():
    """channel_table must sum parallel channels and skip capacity-less ones."""
    import networkx as nx

    graph = nx.MultiDiGraph()
    for scid, capacity in ((1, 500), (2, 300)):  # two parallel channels A<->B
        graph.add_edge("A", "B", key=scid, scid=scid, capacity_sat=capacity)
        graph.add_edge("B", "A", key=scid, scid=scid, capacity_sat=capacity)
    graph.add_edge("A", "C", key=3, scid=3)  # no capacity anywhere
    graph.add_edge("C", "A", key=3, scid=3)

    table = channel_table(graph)
    assert table == [("A", "B", 800)]
