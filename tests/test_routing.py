"""Unit tests for routing and payment simulation on small graphs."""

from lnhistoryclient.analysis.payments import simulate_payment, simulate_random_payments
from lnhistoryclient.analysis.routing import Constraint, ReverseDijkstraRouter, RoutingIndex
from lnhistoryclient.graph import build_multidigraph
from tests.conftest import NODE_A, NODE_B, NODE_C, channel_announcement, channel_update


def test_direct_payment_has_no_fee(line_graph_messages):
    # A -> B has no forwarder at all: A originates, B receives.
    graph = build_multidigraph(line_graph_messages)
    result = simulate_payment(graph, NODE_A, NODE_B, amount_sat=1000)
    assert result.success
    assert result.route.num_hops == 1
    assert result.route.total_fee_msat == 0
    assert result.route.total_cltv == 0


def test_two_hop_payment_charges_intermediate_only():
    """A -> B -> C: only B forwards, so only B's policy on B->C is charged.

    Policies are deliberately asymmetric. A symmetric fixture makes this assertion pass
    for either the correct or the incorrect end-exclusion, which is how the
    sender-charges-itself bug survived to v4.0.0.
    """
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        # A's own outgoing policy: ruinously expensive, and never paid by anyone.
        channel_update(1, 0, fee_base_msat=9_000_000, fee_ppm=0),
        # B's forwarding policy: the only fee on this route.
        channel_update(2, 0, fee_base_msat=7, fee_ppm=0),
    ]
    result = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_C, amount_sat=1000)
    assert result.success
    assert result.route.path == [NODE_A, NODE_B, NODE_C]
    assert result.route.num_hops == 2
    assert result.route.total_fee_msat == 7  # not 9_000_000
    assert result.route.total_cltv == 40  # B's delta only, not A's as well


def test_amount_grows_upstream():
    """The first hop must carry the payment *plus* every downstream fee."""
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_update(1, 0, fee_base_msat=0, fee_ppm=0),
        channel_update(2, 0, fee_base_msat=500, fee_ppm=0),  # B charges 500 msat
    ]
    route = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_C, amount_sat=1000).route
    first, second = route.hops
    assert second.amount_forwarded_msat == 1_000_000  # C receives exactly the payment
    assert first.amount_forwarded_msat == 1_000_500  # B receives payment + its own fee
    assert first.fee_msat == 0  # A originates
    assert second.fee_msat == 500  # B forwards
    assert route.amount_sent_msat == 1_000_500


def test_hops_report_policy_and_scid():
    msgs = [
        channel_announcement(7, NODE_A, NODE_B),
        channel_update(7, 0, htlc_min_msat=2000, htlc_max_msat=9_000_000),
    ]
    hop = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_B, amount_sat=1000).route.hops[0]
    assert hop.scid == 7
    assert hop.direction == 0
    assert (hop.htlc_minimum_msat, hop.htlc_maximum_msat) == (2000, 9_000_000)
    assert hop.cltv_expiry_delta == 40


def test_same_node_fails():
    graph = build_multidigraph([channel_announcement(1, NODE_A, NODE_B), channel_update(1, 0)])
    result = simulate_payment(graph, NODE_A, NODE_A)
    assert not result.success and result.failure_reason == "same_node"


def test_disconnected_pair_is_unreachable():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0),
        channel_update(1, 1),
        channel_announcement(2, NODE_C, "dd" * 33),
        channel_update(2, 0),
        channel_update(2, 1),
    ]
    result = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_C)
    assert not result.success
    assert result.failure_reason == "unreachable"


def test_disabled_direction_is_diagnosed():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, disabled=True),  # A->B disabled
        channel_update(1, 1, disabled=False),
    ]
    result = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_B, amount_sat=1000)
    assert not result.success
    assert result.failure_reason == "disabled_channels"


def test_direction_without_update_is_unroutable():
    """A direction with no channel_update has no known policy, so it cannot be used.

    Before 4.1.0 the missing fee fields defaulted to 0, which made such directions look
    *free* — so fee-minimising Dijkstra preferred them over every priced channel.
    """
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 1),  # B->A only; A->B has no policy at all
    ]
    graph = build_multidigraph(msgs)
    failed = simulate_payment(graph, NODE_A, NODE_B, amount_sat=1000)
    assert not failed.success and failed.failure_reason == "no_channel_update"
    assert simulate_payment(graph, NODE_B, NODE_A, amount_sat=1000).success


def test_unpriced_detour_is_not_preferred_over_priced_direct():
    """A->B priced, vs a policy-less detour A->C->B. The detour must not win."""
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_A, NODE_C),
        channel_announcement(3, NODE_C, NODE_B),
        channel_update(1, 0, fee_base_msat=1000, fee_ppm=1),
        # channels 2 and 3 carry no update -> unknown policy, not free
    ]
    result = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_B, amount_sat=1000)
    assert result.success
    assert result.route.path == [NODE_A, NODE_B]


def test_amount_above_htlc_max_excluded():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, htlc_max_msat=1_000_000),  # 1000 sat max
    ]
    graph = build_multidigraph(msgs)
    assert simulate_payment(graph, NODE_A, NODE_B, amount_sat=1000).success
    too_big = simulate_payment(graph, NODE_A, NODE_B, amount_sat=2000)
    assert not too_big.success and too_big.failure_reason == "htlc_bounds"


def test_htlc_max_applies_to_the_amount_actually_forwarded():
    """The first hop carries payment + downstream fees, so its htlc_max sees more.

    A flat forward search tests every edge against the base amount and would wrongly
    accept this route.
    """
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        # A->B can carry exactly the payment, but not the payment plus B's fee.
        channel_update(1, 0, htlc_max_msat=1_000_000, fee_base_msat=0, fee_ppm=0),
        channel_update(2, 0, fee_base_msat=1000, fee_ppm=0),
    ]
    result = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_C, amount_sat=1000)
    assert not result.success and result.failure_reason == "htlc_bounds"


def test_cheapest_route_is_chosen():
    """A->X->B (one pricey forwarder) vs A->Y->Z->B (two cheap ones).

    A's own outgoing policy toward Y is set ruinously high. Since the sender never pays
    it, the longer route must still win — so this also pins the pathfinding *objective*,
    not just the reported total.
    """
    node_x, node_y, node_z = NODE_C, "dd" * 33, "ee" * 33
    msgs = [
        channel_announcement(1, NODE_A, node_x),
        channel_announcement(2, node_x, NODE_B),
        channel_announcement(3, NODE_A, node_y),
        channel_announcement(4, node_y, node_z),
        channel_announcement(5, node_z, NODE_B),
        channel_update(1, 0, fee_base_msat=0, fee_ppm=0),  # A's own: free
        channel_update(2, 0, fee_base_msat=1_000_000, fee_ppm=0),  # X forwards, pricey
        channel_update(3, 0, fee_base_msat=5_000_000, fee_ppm=0),  # A's own: a decoy
        channel_update(4, 0, fee_base_msat=1, fee_ppm=0),  # Y forwards, cheap
        channel_update(5, 0, fee_base_msat=1, fee_ppm=0),  # Z forwards, cheap
    ]
    result = simulate_payment(build_multidigraph(msgs), NODE_A, NODE_B, amount_sat=1000)
    assert result.success
    assert result.route.path == [NODE_A, node_y, node_z, NODE_B]
    assert result.route.total_fee_msat == 2  # Y + Z; A's 5_000_000 decoy excluded


def test_parallel_channels_pick_the_cheapest_usable_one():
    """Two channels between the same pair; the router must evaluate each separately."""
    msgs = [
        channel_announcement(10, NODE_A, NODE_B),
        channel_announcement(11, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_update(10, 0),
        channel_update(11, 0),
        channel_update(2, 0),
    ]
    graph = build_multidigraph(msgs)
    # Both A->B parallels exist, so the B->C hop is what is priced; the chosen A->B
    # channel must still be one of the two real scids rather than a merged phantom.
    route = simulate_payment(graph, NODE_A, NODE_C, amount_sat=1000).route
    assert route.hops[0].scid in (10, 11)
    assert route.hops[1].scid == 2


def test_random_payments_summary_is_reproducible(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    s1 = simulate_random_payments(graph, n=20, amount_sat=1000, seed=42)
    s2 = simulate_random_payments(graph, n=20, amount_sat=1000, seed=42)
    assert s1.trials == 20
    assert s1.num_success == s2.num_success  # deterministic under seed
    assert 0.0 <= s1.success_rate <= 1.0
    assert s1.num_success + s1.num_failure == 20


def test_routing_index_is_reusable(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    index = RoutingIndex(graph)
    first = simulate_payment(graph, NODE_A, NODE_C, amount_sat=1000, index=index)
    second = simulate_payment(graph, NODE_A, NODE_C, amount_sat=1000, index=index)
    assert first.success and second.success
    assert first.route.path == second.route.path


def test_constraints_can_be_relaxed_explicitly():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, disabled=True),
    ]
    graph = build_multidigraph(msgs)
    index = RoutingIndex(graph)
    router = ReverseDijkstraRouter()
    # This graph has no balances, so BALANCE must be dropped as well (see below).
    usable = Constraint.ALL & ~(Constraint.DISABLED | Constraint.BALANCE)
    assert router.find_route(index, NODE_A, NODE_B, 1000, Constraint.ALL) is None
    assert router.find_route(index, NODE_A, NODE_B, 1000, usable) is not None


def test_balance_constraint_is_fail_closed_without_balances(line_graph_messages):
    """Requiring balances on a graph that has none rejects every edge, deliberately.

    Treating "no balance assigned" as unconstrained would make the router prefer exactly
    the channels whose liquidity is unknown. simulate_payment drops the BALANCE tier
    automatically when the graph carries no balances, so this only bites callers who
    reach past it into the router.
    """
    graph = build_multidigraph(line_graph_messages)
    index = RoutingIndex(graph)
    router = ReverseDijkstraRouter()
    assert router.find_route(index, NODE_A, NODE_C, 1000, Constraint.ALL) is None
    assert simulate_payment(graph, NODE_A, NODE_C, amount_sat=1000).success


def test_custom_strategy_accepted(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    result = simulate_payment(graph, NODE_A, NODE_C, amount_sat=1000, strategy=ReverseDijkstraRouter())
    assert result.success


def test_simple_digraph_is_rejected(line_graph_messages):
    """Collapsing parallels would let one payment draw on several channels' liquidity."""
    import pytest

    from lnhistoryclient.graph import to_directed_simple

    collapsed = to_directed_simple(build_multidigraph(line_graph_messages))
    with pytest.raises(TypeError, match="MultiDiGraph"):
        simulate_payment(collapsed, NODE_A, NODE_C, amount_sat=1000)
