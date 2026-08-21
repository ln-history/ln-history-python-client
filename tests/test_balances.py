"""Unit tests for synthetic balance assignment and liquidity-aware routing."""

import numpy as np
import pytest

from lnhistoryclient.analysis.payments import simulate_payment, simulate_random_payments
from lnhistoryclient.graph import attach_capacity, build_multidigraph
from lnhistoryclient.graph.balances import (
    BALANCES_KEY,
    LOCAL_BALANCE_ATTR,
    Balanced,
    Beta,
    Custom,
    Normal,
    Orientation,
    Polarised,
    Uniform,
    assign_balances,
    load_balances,
    save_balances,
    set_balance,
)
from tests.conftest import NODE_A, NODE_B, NODE_C, channel_announcement, channel_update

CAPACITIES = {1: 1_000_000, 2: 2_000_000}


def _graph(**capacities):
    """A -- B -- C line graph with both directions updated and capacity attached."""
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_update(1, 0),
        channel_update(1, 1),
        channel_update(2, 0),
        channel_update(2, 1),
    ]
    graph = build_multidigraph(msgs)
    attach_capacity(graph, capacities or CAPACITIES)
    return graph


def _invariant_holds(graph):
    """local(0) + local(1) == capacity for every channel."""
    return all(
        graph[u][v][k][LOCAL_BALANCE_ATTR] + graph[v][u][k][LOCAL_BALANCE_ATTR] == graph[u][v][k]["capacity_sat"]
        for u, v, k in graph.edges(keys=True)
    )


# ── distributions ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "distribution",
    [Balanced(), Uniform(), Beta(5, 5), Beta(0.5, 0.5), Normal(0.5, 0.2), Polarised()],
)
def test_every_distribution_preserves_the_capacity_invariant(distribution):
    graph = _graph()
    assign_balances(graph, distribution, seed=1)
    assert _invariant_holds(graph)
    assert set(graph.graph[BALANCES_KEY]) == {1, 2}


def test_balanced_splits_evenly():
    graph = _graph()
    assign_balances(graph, Balanced())
    assert graph.graph[BALANCES_KEY] == {1: 500_000, 2: 1_000_000}


def test_clip_bounds_model_the_channel_reserve():
    """lo/hi keep a slice unspendable, reproducing channel_reserve_satoshis."""
    graph = _graph()
    # Polarised piles mass at both ends, so without clipping some channels hit 0 or full.
    assign_balances(graph, Polarised(lo=0.01, hi=0.99), seed=3)
    for scid, share in graph.graph[BALANCES_KEY].items():
        capacity = CAPACITIES[scid]
        assert 0.01 * capacity <= share <= 0.99 * capacity


def test_clip_bounds_are_validated():
    with pytest.raises(ValueError, match="clip bounds"):
        Beta(2, 2, lo=0.9, hi=0.1)


def test_assignment_is_reproducible_under_seed():
    first, second = _graph(), _graph()
    assign_balances(first, Beta(2, 5), seed=42)
    assign_balances(second, Beta(2, 5), seed=42)
    assert first.graph[BALANCES_KEY] == second.graph[BALANCES_KEY]


def test_custom_distribution_is_accepted():
    graph = _graph()
    assign_balances(graph, Custom(lambda n, rng: np.full(n, 0.25), name="quarter"))
    assert graph.graph[BALANCES_KEY] == {1: 250_000, 2: 500_000}
    assert "quarter" in graph.graph["balance_scenario"].distribution


# ── capacity policy ──────────────────────────────────────────────────────────────


def test_strict_mode_requires_real_capacity():
    graph = build_multidigraph([channel_announcement(1, NODE_A, NODE_B), channel_update(1, 0), channel_update(1, 1)])
    with pytest.raises(ValueError, match="capacity_sat"):
        assign_balances(graph, Balanced())


def test_non_strict_mode_falls_back_to_the_htlc_max_proxy():
    graph = build_multidigraph(
        [
            channel_announcement(1, NODE_A, NODE_B),
            channel_update(1, 0, htlc_max_msat=4_000_000),  # 4000 sat proxy
            channel_update(1, 1, htlc_max_msat=4_000_000),
        ]
    )
    scenario = assign_balances(graph, Balanced(), strict=False)
    assert scenario.capacity_source == "htlc_max_proxy"
    assert graph.graph[BALANCES_KEY] == {1: 2000}


# ── orientation ──────────────────────────────────────────────────────────────────


def test_by_degree_orientation_favours_the_hub():
    """B has two channels, A and C one each: the skew must land on B both times."""
    graph = _graph()
    # Beta(9, 1) puts ~90% on whichever endpoint the orientation selects.
    assign_balances(graph, Beta(9, 1), seed=5, orientation=Orientation.BY_DEGREE)
    # scid 1 is A->B (node_1 = A), scid 2 is B->C (node_1 = B).
    a_side = graph.graph[BALANCES_KEY][1]
    b_side = graph.graph[BALANCES_KEY][2]
    assert a_side < CAPACITIES[1] / 2  # hub B holds the larger share of channel 1
    assert b_side > CAPACITIES[2] / 2  # hub B holds the larger share of channel 2


def test_correlated_assignment_drains_a_node_consistently():
    """One latent level per node means a drained node is drained on all its channels."""
    graph = _graph()
    assign_balances(graph, Polarised(), seed=11, correlate=True)
    scenario = graph.graph["balance_scenario"]
    assert scenario.correlate is True
    assert _invariant_holds(graph)
    # B's share of both its channels derives from the same latent level, so the two
    # fractions must agree in direction relative to its partners.
    b_in_ch1 = CAPACITIES[1] - graph.graph[BALANCES_KEY][1]  # B is node_2 of channel 1
    b_in_ch2 = graph.graph[BALANCES_KEY][2]  # B is node_1 of channel 2
    assert (b_in_ch1 / CAPACITIES[1] > 0.5) == (b_in_ch2 / CAPACITIES[2] > 0.5)


# ── manual writes ────────────────────────────────────────────────────────────────


def test_set_balance_mirrors_both_directions():
    graph = _graph()
    assign_balances(graph, Balanced())
    set_balance(graph, 1, 900_000)
    assert graph[NODE_A][NODE_B][1][LOCAL_BALANCE_ATTR] == 900_000
    assert graph[NODE_B][NODE_A][1][LOCAL_BALANCE_ATTR] == 100_000
    assert _invariant_holds(graph)


def test_set_balance_rejects_an_impossible_split():
    graph = _graph()
    assign_balances(graph, Balanced())
    with pytest.raises(ValueError, match="outside"):
        set_balance(graph, 1, 2_000_000)  # exceeds the 1_000_000 capacity


# ── liquidity-aware routing ──────────────────────────────────────────────────────


def test_payment_blocked_by_liquidity_is_diagnosed():
    graph = _graph()
    assign_balances(graph, Balanced())  # 500k sat each side of channel 1
    result = simulate_payment(graph, NODE_A, NODE_B, amount_sat=900_000)
    assert not result.success
    assert result.failure_reason == "insufficient_liquidity"


def test_payment_within_liquidity_succeeds_and_reports_balances():
    graph = _graph()
    assign_balances(graph, Balanced())
    result = simulate_payment(graph, NODE_A, NODE_B, amount_sat=100_000)
    assert result.success
    hop = result.route.hops[0]
    assert hop.balance_before_sat == 500_000
    assert hop.balance_after_sat == 400_000


def test_commit_moves_liquidity_and_is_off_by_default():
    graph = _graph()
    assign_balances(graph, Balanced())

    simulate_payment(graph, NODE_A, NODE_B, amount_sat=100_000)
    assert graph.graph[BALANCES_KEY][1] == 500_000  # unchanged: commit is opt-in

    simulate_payment(graph, NODE_A, NODE_B, amount_sat=100_000, commit=True)
    assert graph.graph[BALANCES_KEY][1] == 400_000  # A's share fell
    assert graph[NODE_B][NODE_A][1][LOCAL_BALANCE_ATTR] == 600_000  # B's side rose
    assert _invariant_holds(graph)


def test_repeated_commits_exhaust_a_channel():
    graph = _graph()
    assign_balances(graph, Balanced())
    for _ in range(5):
        assert simulate_payment(graph, NODE_A, NODE_B, amount_sat=100_000, commit=True).success
    exhausted = simulate_payment(graph, NODE_A, NODE_B, amount_sat=100_000, commit=True)
    assert not exhausted.success
    assert exhausted.failure_reason == "insufficient_liquidity"
    assert graph.graph[BALANCES_KEY][1] == 0


def test_sequential_run_depletes_while_independent_run_does_not():
    independent, sequential = _graph(), _graph()
    assign_balances(independent, Balanced(), seed=1)
    assign_balances(sequential, Balanced(), seed=1)

    simulate_random_payments(independent, n=30, amount_sat=100_000, seed=7)
    summary = simulate_random_payments(sequential, n=30, amount_sat=100_000, seed=7, sequential=True)

    assert independent.graph[BALANCES_KEY] == {1: 500_000, 2: 1_000_000}
    assert sequential.graph[BALANCES_KEY] != independent.graph[BALANCES_KEY]
    assert summary.sequential is True


def test_summary_records_the_scenario():
    graph = _graph()
    assign_balances(graph, Polarised(), seed=2)
    summary = simulate_random_payments(graph, n=10, amount_sat=1000, seed=3)
    assert "Polarised" in summary.balance_scenario
    assert summary.channels_without_balance == 0


# ── persistence ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("suffix", [".csv", ".json", ".parquet"])
def test_balances_round_trip(tmp_path, suffix):
    source = _graph()
    assign_balances(source, Beta(2, 5), seed=9)
    path = str(tmp_path / f"balances{suffix}")
    save_balances(source, path)

    target = _graph()
    assert load_balances(target, path) == 2
    assert target.graph[BALANCES_KEY] == source.graph[BALANCES_KEY]
    assert _invariant_holds(target)


def test_saving_without_balances_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="assign_balances"):
        save_balances(_graph(), str(tmp_path / "empty.csv"))


def test_unknown_suffix_is_rejected(tmp_path):
    graph = _graph()
    assign_balances(graph, Balanced())
    with pytest.raises(ValueError, match="unsupported"):
        save_balances(graph, str(tmp_path / "balances.txt"))


def test_zero_channel_nodes_are_not_selected():
    """Announced nodes with no channels cannot transact, so they must not be drawn.

    build_multidigraph keeps every announced node; on a 2021 snapshot 38% of them have
    no channels at all. Selecting them would manufacture guaranteed failures.
    """
    from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement

    lonely = "ff" * 33
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0),
        channel_update(1, 1),
        NodeAnnouncement(
            signature=b"",
            features=b"",
            timestamp=100,
            node_id=bytes.fromhex(lonely),
            rgb_color=b"\x00\x00\x00",
            alias=b"lonely",
            addresses=b"",
        ),
    ]
    graph = build_multidigraph(msgs)
    attach_capacity(graph, {1: 1_000_000})
    assign_balances(graph, Balanced())

    assert lonely in graph  # present in the graph...
    summary = simulate_random_payments(graph, n=25, amount_sat=1000, seed=1)
    assert summary.eligible_nodes == 2  # ...but never eligible
    assert all(lonely not in (r.src, r.dst) for r in summary.results)
