"""Unit tests for the paired client-comparison surface.

These cover the parts the comparative study leans on: that the trial pool can be
restricted, that the restriction is *stable* (an unstable pool silently unpairs the
comparison), and that the tidy frames keep failed trials rather than dropping them.
"""

from lnhistoryclient.analysis.compare import build_trials, compare_clients
from lnhistoryclient.analysis.routing import Constraint, RoutingIndex
from lnhistoryclient.graph import build_multidigraph
from tests.conftest import NODE_A, NODE_B, NODE_C, channel_announcement, channel_update

NODE_D = "dd" * 33
NODE_E = "ee" * 33


def _ring_with_stub():
    """A -> B -> C -> A fully updated, plus a leaf D reachable only one way.

    The ring is the routable core; D can be paid but cannot pay, so it belongs to no
    strongly connected component larger than itself.
    """
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_announcement(3, NODE_C, NODE_A),
        channel_announcement(4, NODE_C, NODE_D),
    ]
    for scid in (1, 2, 3):
        msgs.append(channel_update(scid, 0, fee_base_msat=1, fee_ppm=0))
        msgs.append(channel_update(scid, 1, fee_base_msat=1, fee_ppm=0))
    # Only C -> D carries a policy, so D has no outgoing route.
    msgs.append(channel_update(4, 0, fee_base_msat=1, fee_ppm=0))
    return build_multidigraph(msgs)


def test_routable_core_excludes_one_way_nodes():
    core = RoutingIndex(_ring_with_stub()).routable_core(amount_sat=1000)
    assert core == {NODE_A, NODE_B, NODE_C}


def test_routable_core_drops_directions_without_a_policy():
    """A direction with no channel_update is unusable, so it cannot hold the core together."""
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_A),
        channel_update(1, 0, fee_base_msat=1, fee_ppm=0),
        channel_update(1, 1, fee_base_msat=1, fee_ppm=0),
        # scid 2 announced but never updated in either direction.
    ]
    core = RoutingIndex(build_multidigraph(msgs)).routable_core(amount_sat=1000)
    assert core == {NODE_A, NODE_B}

    without_policy = RoutingIndex(build_multidigraph(msgs)).routable_core(amount_sat=1000, constraints=Constraint.NONE)
    assert without_policy == {NODE_A, NODE_B}


def test_routable_core_shrinks_as_the_amount_grows():
    """htlc_maximum_msat is amount-dependent, so the core is a function of the payment."""
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, fee_base_msat=0, fee_ppm=0, htlc_max_msat=5_000_000),
        channel_update(1, 1, fee_base_msat=0, fee_ppm=0, htlc_max_msat=5_000_000),
    ]
    index = RoutingIndex(build_multidigraph(msgs))
    assert index.routable_core(amount_sat=1_000) == {NODE_A, NODE_B}
    # 1 000 000 sat = 1e9 msat, well past the 5e6 msat htlc_maximum on both directions.
    assert index.routable_core(amount_sat=1_000_000) == set()


def test_routable_core_is_stable_across_calls():
    """The trial pool is derived from this; a pool that moved would unpair the study."""
    index = RoutingIndex(_ring_with_stub())
    assert index.routable_core(1000) == index.routable_core(1000)


def test_trials_can_be_restricted_to_a_node_pool():
    graph = _ring_with_stub()
    core = RoutingIndex(graph).routable_core(amount_sat=1000)
    trials = build_trials(graph, n=25, amount_sat=1000, seed=1, nodes=core)
    assert len(trials) == 25
    assert all(src in core and dst in core for src, dst, _ in trials)
    assert all(src != dst for src, dst, _ in trials)


def test_trials_are_reproducible_from_the_seed():
    """Cells regenerate their own trial list, so equal seeds must mean equal trials."""
    graph = _ring_with_stub()
    core = RoutingIndex(graph).routable_core(amount_sat=1000)
    first = build_trials(graph, n=20, amount_sat=1000, seed=7, nodes=core)
    second = build_trials(graph, n=20, amount_sat=1000, seed=7, nodes=core)
    assert first == second


def test_unknown_pool_members_are_ignored():
    graph = _ring_with_stub()
    trials = build_trials(graph, n=10, amount_sat=1000, seed=3, nodes=[NODE_A, NODE_B, NODE_E])
    assert {node for src, dst, _ in trials for node in (src, dst)} <= {NODE_A, NODE_B}


def _two_client_comparison():
    """A -> B -> C, so exactly one route exists and B is the only forwarder.

    The second trial starts at a node with no channels at all, so it is unroutable for
    every client — which is what makes the failed-row behaviour observable.
    """
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_update(1, 0, fee_base_msat=0, fee_ppm=0),
        channel_update(2, 0, fee_base_msat=700, fee_ppm=0),
    ]
    graph = build_multidigraph(msgs)
    graph.add_node(NODE_E)
    trials = [(NODE_A, NODE_C, 1000), (NODE_E, NODE_A, 1000)]
    return compare_clients(graph, ["cln-pay@24.02.1", "ldk@0.0.120"], trials=trials, diagnose=False)


def test_route_frame_keeps_failed_trials():
    """Dropping them would let a groupby compare clients over different trial sets."""
    frame = _two_client_comparison().route_frame()
    assert len(frame) == 4  # 2 trials x 2 clients
    assert set(frame["success"]) == {True, False}
    failed = frame[~frame["success"]]
    assert failed["route_key"].isna().all()
    assert failed["fee_msat"].isna().all()


def test_route_key_identifies_the_channel_sequence():
    """Two clients agree on a trial exactly when their route_key matches."""
    frame = _two_client_comparison().route_frame()
    routed = frame[frame["success"]]
    assert set(routed["route_key"]) == {"1-2"}  # A -> B -> C, the only route
    assert len(routed) == 2  # both clients found it, so both agree


def test_hop_frame_credits_the_forwarder_not_the_sender():
    frame = _two_client_comparison().hop_frame()
    first = frame[(frame["client"] == "cln-pay@24.02.1") & (frame["hop_index"] == 0)]
    assert (first["fee_msat"] == 0).all()  # the sender originates rather than forwards
    assert (first["src"] == NODE_A).all()
    forwarding = frame[frame["hop_index"] == 1]
    assert (forwarding["src"] == NODE_B).all()
    assert (forwarding["fee_msat"] == 700).all()  # B's policy on B -> C, charged once
