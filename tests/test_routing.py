"""Unit tests for routing and payment simulation on small graphs."""

from lnhistoryclient.analysis.payments import simulate_payment, simulate_random_payments
from lnhistoryclient.analysis.routing import CheapestFeeRouter, routable_view
from lnhistoryclient.graph import build_multidigraph, to_directed_simple
from tests.conftest import NODE_A, NODE_B, NODE_C, channel_announcement, channel_update


def test_direct_payment_has_no_fee(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    result = simulate_payment(graph, NODE_A, NODE_B, amount_sat=1000)
    assert result.success
    assert result.route.hops == 1
    assert result.route.total_fee_msat == 0  # recipient charges nothing


def test_two_hop_payment_charges_intermediate_only(line_graph_messages):
    # A -> B -> C ; only B (the intermediate) charges a fee; C is recipient.
    graph = build_multidigraph(line_graph_messages)
    result = simulate_payment(graph, NODE_A, NODE_C, amount_sat=1000)
    assert result.success
    assert result.route.path == [NODE_A, NODE_B, NODE_C]
    assert result.route.hops == 2
    # exactly one hop's fee counts; default fee_base=1000, ppm=1 on 1000 sat
    expected = 1000 + (1000 * 1000 * 1) // 1_000_000
    assert result.route.total_fee_msat == expected


def test_same_node_fails():
    graph = build_multidigraph([channel_announcement(1, NODE_A, NODE_B), channel_update(1, 0)])
    result = simulate_payment(graph, NODE_A, NODE_A)
    assert not result.success and result.failure_reason == "same_node"


def test_disconnected_pair_has_no_route():
    # two separate channels sharing no node
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0),
        channel_update(1, 1),
        channel_announcement(2, NODE_C, "dd" * 33),
        channel_update(2, 0),
        channel_update(2, 1),
    ]
    graph = build_multidigraph(msgs)
    result = simulate_payment(graph, NODE_A, NODE_C)
    assert not result.success and result.failure_reason == "no_route"


def test_disabled_direction_excluded():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, disabled=True),  # A->B disabled
        channel_update(1, 1, disabled=False),
    ]
    graph = build_multidigraph(msgs)
    result = simulate_payment(graph, NODE_A, NODE_B, amount_sat=1000)
    assert not result.success and result.failure_reason == "no_route"


def test_amount_above_htlc_max_excluded():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, htlc_max_msat=1_000_000),  # 1000 sat max
    ]
    graph = build_multidigraph(msgs)
    ok = simulate_payment(graph, NODE_A, NODE_B, amount_sat=1000)
    too_big = simulate_payment(graph, NODE_A, NODE_B, amount_sat=2000)
    assert ok.success
    assert not too_big.success and too_big.failure_reason == "no_route"


def test_cheapest_route_is_chosen():
    # A->B directly (expensive) vs A->C->B (two cheap hops); router minimises fee.
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_A, NODE_C),
        channel_announcement(3, NODE_C, NODE_B),
        channel_update(1, 0, fee_base_msat=1_000_000, fee_ppm=0),  # direct, pricey
        channel_update(2, 0, fee_base_msat=1, fee_ppm=0),
        channel_update(3, 0, fee_base_msat=1, fee_ppm=0),
    ]
    graph = build_multidigraph(msgs)
    result = simulate_payment(graph, NODE_A, NODE_B, amount_sat=1000)
    assert result.success
    assert result.route.path == [NODE_A, NODE_C, NODE_B]


def test_random_payments_summary_is_reproducible(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    s1 = simulate_random_payments(graph, n=20, amount_sat=1000, seed=42)
    s2 = simulate_random_payments(graph, n=20, amount_sat=1000, seed=42)
    assert s1.trials == 20
    assert s1.num_success == s2.num_success  # deterministic under seed
    assert 0.0 <= s1.success_rate <= 1.0
    assert s1.num_success + s1.num_failure == 20


def test_routable_view_attaches_fee():
    msgs = [channel_announcement(1, NODE_A, NODE_B), channel_update(1, 0, fee_base_msat=500, fee_ppm=0)]
    graph = build_multidigraph(msgs)
    view = routable_view(to_directed_simple(graph), amount_sat=1000)
    assert view[NODE_A][NODE_B]["_fee_msat"] == 500


def test_custom_strategy_accepted(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    result = simulate_payment(graph, NODE_A, NODE_C, amount_sat=1000, strategy=CheapestFeeRouter())
    assert result.success
