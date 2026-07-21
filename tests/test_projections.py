"""Unit tests for graph building and projection (parallel-channel handling)."""

from lnhistoryclient.graph import build_multidigraph, to_directed_simple, to_undirected_simple
from lnhistoryclient.graph.projections import edge_capacity_sat
from tests.conftest import NODE_A, NODE_B, channel_announcement, channel_update


def test_builder_creates_two_directed_edges_per_channel(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    assert graph.number_of_nodes() == 3
    assert graph.number_of_edges() == 4  # 2 channels x 2 directions


def test_direction_and_disabled_flags():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, disabled=False),
        channel_update(1, 1, disabled=True),
    ]
    graph = build_multidigraph(msgs)
    assert graph.get_edge_data(NODE_A, NODE_B, key=1)["direction"] == 0
    assert graph.get_edge_data(NODE_A, NODE_B, key=1)["disabled"] is False
    assert graph.get_edge_data(NODE_B, NODE_A, key=1)["disabled"] is True


def test_htlc_max_capacity_proxy_msat_to_sat():
    msgs = [channel_announcement(1, NODE_A, NODE_B), channel_update(1, 0, htlc_max_msat=5_000_000)]
    graph = build_multidigraph(msgs)
    attrs = graph.get_edge_data(NODE_A, NODE_B, key=1)
    assert edge_capacity_sat(attrs) == 5000  # 5_000_000 msat // 1000


def test_latest_update_wins():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_update(1, 0, fee_base_msat=1000, timestamp=100),
        channel_update(1, 0, fee_base_msat=9999, timestamp=200),
        channel_update(1, 0, fee_base_msat=5, timestamp=50),
    ]
    graph = build_multidigraph(msgs)
    assert graph.get_edge_data(NODE_A, NODE_B, key=1)["fee_base_msat"] == 9999


def test_directed_simple_collapses_parallels_cheapest_and_sums_capacity():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_A, NODE_B),  # parallel channel
        channel_update(1, 0, fee_base_msat=1000, htlc_max_msat=5_000_000),
        channel_update(2, 0, fee_base_msat=500, htlc_max_msat=9_000_000),
    ]
    graph = build_multidigraph(msgs)
    d = to_directed_simple(graph)
    edge = d.get_edge_data(NODE_A, NODE_B)
    assert edge["fee_base_msat"] == 500  # cheapest kept
    assert edge["capacity_sat"] == 5000 + 9000  # summed
    assert edge["num_parallel"] == 2


def test_directed_simple_disabled_only_if_all_parallels_disabled():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_A, NODE_B),
        channel_update(1, 0, disabled=True),
        channel_update(2, 0, disabled=False),
    ]
    graph = build_multidigraph(msgs)
    d = to_directed_simple(graph)
    assert d.get_edge_data(NODE_A, NODE_B)["disabled"] is False


def test_undirected_simple_dedupes_scid_across_directions():
    msgs = [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_A, NODE_B),
        channel_update(1, 0, htlc_max_msat=5_000_000),
        channel_update(1, 1, htlc_max_msat=5_000_000),
        channel_update(2, 0, htlc_max_msat=9_000_000),
    ]
    graph = build_multidigraph(msgs)
    u = to_undirected_simple(graph)
    edge = u.get_edge_data(NODE_A, NODE_B)
    assert edge["num_channels"] == 2  # two distinct scids, not 4 directed edges
    assert edge["capacity_sat"] == 5000 + 9000


def test_channel_only_node_is_unannounced():
    msgs = [channel_announcement(1, NODE_A, NODE_B)]
    graph = build_multidigraph(msgs)
    assert graph.nodes[NODE_A]["announced"] is False
