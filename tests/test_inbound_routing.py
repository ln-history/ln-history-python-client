"""Inbound-fee-aware route search: correctness against brute force, and behaviour."""

import itertools
import random
from typing import Dict, Optional

import networkx as nx
import pytest

from lnhistoryclient.analysis.inbound_routing import RISK_FACTOR_BILLIONTHS, Route, find_route
from lnhistoryclient.analysis.weights import fee_msat, inbound_fee_msat

AMOUNT = 100_000
AMOUNT_MSAT = AMOUNT * 1000


def _edge(base: int, ppm: int, cltv: int = 40, inbound_base: int = 0, inbound_rate: int = 0) -> Dict[str, object]:
    """A usable directed edge. ``inbound_*`` is what the edge's DESTINATION charges."""
    return {
        "has_update": True,
        "disabled": False,
        "fee_base_msat": base,
        "fee_proportional_millionths": ppm,
        "cltv_expiry_delta": cltv,
        "htlc_minimum_msat": 0,
        "htlc_maximum_msat": 10**12,
        "capacity_sat": 10**9,
        "dst_inbound_fee_base_msat": inbound_base,
        "dst_inbound_fee_proportional_millionths": inbound_rate,
    }


def _brute_force(
    graph: nx.MultiDiGraph,
    source: str,
    target: str,
    amount_sat: int,
    inbound: bool,
    risk_factor: int,
    max_hops: int = 6,
) -> Optional[int]:
    """Cheapest weight over *every* simple path, by exhaustive enumeration.

    Deliberately naive: it exists to check the A* search, so it must share none of its
    machinery. Returns the best weight, or None if the target is unreachable.
    """
    best: Optional[int] = None
    for path in nx.all_simple_paths(graph, source, target, cutoff=max_hops):
        # Each consecutive node pair may have parallel edges; try every combination.
        # path and path[1:] are intentionally different lengths, so no strict= here.
        steps = list(zip(path, path[1:], strict=False))  # different lengths by design
        choices = [list(graph[u][v].keys()) for u, v in steps]
        for combo in itertools.product(*choices):
            edges = [graph[u][v][k] for (u, v), k in zip(steps, combo, strict=True)]
            weight = 0
            for index, attrs in enumerate(edges):
                cltv = attrs["cltv_expiry_delta"]
                penalty = (AMOUNT_MSAT * cltv * risk_factor) // 1_000_000_000
                if index == 0:
                    fee = 0  # the sender charges nothing
                else:
                    outbound = fee_msat(attrs, amount_sat)
                    signed = (inbound_fee_msat(edges[index - 1], amount_sat) if inbound else 0) + outbound
                    fee = signed if signed > 0 else 0
                weight += fee + penalty
            if best is None or weight < best:
                best = weight
    return best


def _random_graph(seed: int) -> nx.MultiDiGraph:
    """A small random graph with discounts big enough that the zero-clamp bites."""
    rng = random.Random(seed)
    graph = nx.MultiDiGraph()
    nodes = [f"n{i}" for i in range(7)]
    for u, v in itertools.permutations(nodes, 2):
        if rng.random() < 0.45:
            # Inbound values are sometimes large negatives, so max(0, ...) actually fires.
            inbound_rate = rng.choice([0, 0, -50, -500, -5000, 200])
            graph.add_edge(
                u,
                v,
                key=rng.randrange(1, 10**6),
                **_edge(
                    base=rng.choice([0, 100, 1000]),
                    ppm=rng.choice([0, 1, 100, 1000]),
                    cltv=rng.choice([14, 40, 144]),
                    inbound_rate=inbound_rate,
                ),
            )
    return graph


# ── correctness ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", range(25))
@pytest.mark.parametrize("inbound", [True, False])
def test_matches_brute_force(seed: int, inbound: bool) -> None:
    """The A* search must return exactly the optimum an exhaustive walk finds.

    This is the test that licenses the heuristic. The bound substitutes each node's *most
    negative* inbound fee for its actual one, which can only understate the true cost, so
    it is admissible -- but "should be admissible" is an argument, and this is a check.
    """
    graph = _random_graph(seed)
    if "n0" not in graph or "n6" not in graph:
        pytest.skip("degenerate graph")
    expected = _brute_force(graph, "n0", "n6", AMOUNT, inbound, RISK_FACTOR_BILLIONTHS)
    route = find_route(graph, "n0", "n6", AMOUNT, inbound=inbound)
    if expected is None:
        assert route is None
    else:
        assert route is not None
        assert route.weight == expected


def test_unreachable_returns_none_without_exhausting_the_search() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b", key=1, **_edge(0, 0))
    graph.add_edge("c", "d", key=2, **_edge(0, 0))
    assert find_route(graph, "a", "d", AMOUNT) is None


def test_source_equals_target_is_not_a_route() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b", key=1, **_edge(0, 0))
    assert find_route(graph, "a", "a", AMOUNT) is None


def test_unusable_channels_are_skipped() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b", key=1, **_edge(0, 0))
    graph["a"]["b"][1]["disabled"] = True
    graph.add_edge("b", "c", key=2, **_edge(0, 0))
    assert find_route(graph, "a", "c", AMOUNT) is None


def test_htlc_maximum_is_respected() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b", key=1, **_edge(0, 0))
    graph.add_edge("b", "c", key=2, **_edge(0, 0))
    graph["b"]["c"][2]["htlc_maximum_msat"] = AMOUNT_MSAT - 1
    assert find_route(graph, "a", "c", AMOUNT) is None


# ── behaviour ───────────────────────────────────────────────────────────────────────


def test_sender_charges_no_fee_on_the_first_hop() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge("a", "b", key=1, **_edge(1_000_000, 0))  # would dominate if charged
    graph.add_edge("b", "c", key=2, **_edge(0, 0))
    route = find_route(graph, "a", "c", AMOUNT, risk_factor=0)
    assert route is not None
    assert route.fee_msat == 0


def test_inbound_discount_changes_which_route_is_chosen() -> None:
    """The point of the module: a discount can make a dearer-looking path the cheaper one.

    Two parallel two-hop routes. Via ``x`` the outbound fee is lower, but ``t`` charges no
    discount for arriving that way. Via ``y`` the outbound fee is higher, and ``t``
    discounts arrivals on that channel by more than the difference.
    """
    graph = nx.MultiDiGraph()
    graph.add_edge("s", "x", key=1, **_edge(0, 0))
    graph.add_edge("s", "y", key=2, **_edge(0, 0))
    # x -> t: cheap outbound at x, no discount at t for this channel
    graph.add_edge("x", "t", key=3, **_edge(1_000, 0))
    # y -> t: dearer outbound at y, but t discounts arrivals here
    graph.add_edge("y", "t", key=4, **_edge(5_000, 0))
    # The discount t charges is advertised on the reverse edges.
    graph.add_edge("t", "x", key=3, **_edge(0, 0))
    graph.add_edge("t", "y", key=4, **_edge(0, 0))
    # Attach what the *destination* of each edge charges, as build_multidigraph would.
    graph["s"]["x"][1]["dst_inbound_fee_base_msat"] = 0
    graph["s"]["y"][2]["dst_inbound_fee_base_msat"] = -4_500  # y discounts arrivals from s

    outbound_only = find_route(graph, "s", "t", AMOUNT, inbound=False, risk_factor=0)
    inbound_aware = find_route(graph, "s", "t", AMOUNT, inbound=True, risk_factor=0)
    assert outbound_only is not None and inbound_aware is not None
    # Outbound-only sees 1,000 via x versus 5,000 via y.
    assert outbound_only.nodes == ["s", "x", "t"]
    assert outbound_only.fee_msat == 1_000
    # Pricing y's discount, the y path costs max(0, -4500 + 5000) = 500.
    assert inbound_aware.nodes == ["s", "y", "t"]
    assert inbound_aware.fee_msat == 500


def test_discount_larger_than_the_fee_clamps_to_zero_not_negative() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge("s", "m", key=1, **_edge(0, 0, inbound_base=-10_000_000))
    graph.add_edge("m", "t", key=2, **_edge(1_000, 0))
    route = find_route(graph, "s", "t", AMOUNT, inbound=True, risk_factor=0)
    assert route is not None
    assert route.fee_msat == 0  # never negative, however large the discount


def test_risk_factor_breaks_the_zero_fee_tie() -> None:
    """With fee alone, a long free path ties with a short one; lnd's time-lock penalty
    is what separates them, and it applies identically with and without inbound fees."""
    graph = nx.MultiDiGraph()
    graph.add_edge("s", "t", key=1, **_edge(0, 0, cltv=40))
    graph.add_edge("s", "a", key=2, **_edge(0, 0, cltv=40))
    graph.add_edge("a", "b", key=3, **_edge(0, 0, cltv=40))
    graph.add_edge("b", "t", key=4, **_edge(0, 0, cltv=40))
    direct = find_route(graph, "s", "t", AMOUNT, risk_factor=RISK_FACTOR_BILLIONTHS)
    assert direct is not None
    assert direct.hops == 1

    tie = find_route(graph, "s", "t", AMOUNT, risk_factor=0)
    assert tie is not None
    assert tie.fee_msat == 0  # both cost nothing; which one is returned is unconstrained


def test_missing_inbound_attributes_degrade_to_outbound_only() -> None:
    """A graph built without ``inbound_fees=True`` has no dst_inbound_* keys."""
    graph = nx.MultiDiGraph()
    plain = _edge(1_000, 0)
    del plain["dst_inbound_fee_base_msat"]
    del plain["dst_inbound_fee_proportional_millionths"]
    graph.add_edge("s", "m", key=1, **plain)
    graph.add_edge("m", "t", key=2, **plain)
    with_flag = find_route(graph, "s", "t", AMOUNT, inbound=True, risk_factor=0)
    without = find_route(graph, "s", "t", AMOUNT, inbound=False, risk_factor=0)
    assert with_flag is not None and without is not None
    assert with_flag.fee_msat == without.fee_msat == 1_000


def test_route_reports_its_nodes_in_order() -> None:
    graph = nx.MultiDiGraph()
    graph.add_edge("s", "m", key=1, **_edge(0, 0))
    graph.add_edge("m", "t", key=2, **_edge(0, 0))
    route = find_route(graph, "s", "t", AMOUNT)
    assert isinstance(route, Route)
    assert route.nodes == ["s", "m", "t"]
    assert route.edges == [("s", "m", 1), ("m", "t", 2)]
