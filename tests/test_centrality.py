"""Unit tests for node ranking on small graphs with known answers."""

import pytest

from lnhistoryclient.analysis.centrality import Metric, top_nodes_by
from lnhistoryclient.graph import build_multidigraph
from tests.conftest import NODE_A, NODE_B, NODE_C


def test_betweenness_middle_of_path_is_top(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    ranked = top_nodes_by(graph, Metric.BETWEENNESS, n=3)
    # In an A-B-C path, B sits on the only shortest path A<->C.
    assert ranked[0].node_id == NODE_B
    assert ranked[0].score == pytest.approx(1.0)
    # endpoints have zero betweenness
    assert {r.node_id for r in ranked if r.score == 0} == {NODE_A, NODE_C}


def test_degree_ranks_by_channel_count(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    ranked = top_nodes_by(graph, Metric.DEGREE, n=3)
    assert ranked[0].node_id == NODE_B
    assert ranked[0].score == 2.0  # B has two channels
    assert ranked[0].num_channels == 2


def test_strength_ranks_by_capacity(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    ranked = top_nodes_by(graph, Metric.STRENGTH, weight="capacity", n=3)
    # htlc_max default 5_000_000_000 msat = 5_000_000 sat per channel; B has 2 channels
    assert ranked[0].node_id == NODE_B
    assert ranked[0].score == pytest.approx(2 * 5_000_000)


def test_pagerank_runs_and_sums_to_one(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    ranked = top_nodes_by(graph, Metric.PAGERANK, n=3)
    assert len(ranked) == 3
    assert sum(r.score for r in ranked) == pytest.approx(1.0, abs=1e-6)


def test_fee_weight_rejected_for_degree(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    with pytest.raises(ValueError):
        top_nodes_by(graph, Metric.DEGREE, weight="fee")


def test_n_limits_result_size(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    assert len(top_nodes_by(graph, Metric.DEGREE, n=1)) == 1


def test_ranks_are_sequential(line_graph_messages):
    graph = build_multidigraph(line_graph_messages)
    ranked = top_nodes_by(graph, Metric.DEGREE, n=3)
    assert [r.rank for r in ranked] == [1, 2, 3]
