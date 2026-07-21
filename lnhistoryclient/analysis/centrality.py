"""Node ranking / centrality over a snapshot graph.

One generic entry point, :func:`top_nodes_by`, selects the measure via a
:class:`Metric` enum and honours an optional ``weight`` (``None`` / ``"fee"`` /
``"capacity"``) following the conventions in :mod:`lnhistoryclient.analysis.weights`.

Which projection each metric runs on:

* ``DEGREE`` / ``STRENGTH`` — undirected simple graph (channel count / capacity sum).
* ``BETWEENNESS`` / ``CLOSENESS`` — undirected simple graph for unweighted and
  capacity-weighted runs (topology); the **directed** simple graph for fee-weighted
  runs, since fees are directional.
* ``PAGERANK`` — directed simple graph (capacity as edge weight when weighted).
* ``EIGENVECTOR`` — undirected simple graph.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

import networkx as nx

from lnhistoryclient.analysis.weights import (
    DEFAULT_AMOUNT_SAT,
    Weighting,
    coerce_weighting,
    edge_strength,
    path_distance,
)
from lnhistoryclient.graph.projections import to_directed_simple, to_undirected_simple

logger = logging.getLogger(__name__)

_DISTANCE_ATTR = "_distance"
_STRENGTH_ATTR = "_strength"


class Metric(str, Enum):
    """Available node-ranking measures."""

    DEGREE = "degree"
    STRENGTH = "strength"
    BETWEENNESS = "betweenness"
    CLOSENESS = "closeness"
    PAGERANK = "pagerank"
    EIGENVECTOR = "eigenvector"


@dataclass
class NodeRank:
    """A single ranked node with context pulled from the snapshot."""

    rank: int
    node_id: str
    score: float
    alias: str
    announced: bool
    num_channels: int
    capacity_sat: Optional[int]


def _largest_component(graph: nx.Graph) -> nx.Graph:
    """Return the subgraph induced by the largest connected component."""
    if graph.number_of_nodes() == 0:
        return graph
    largest = max(nx.connected_components(graph), key=len)
    return graph.subgraph(largest).copy()


def _annotate_distance(graph: nx.Graph, weighting: Weighting, amount_sat: int) -> Optional[str]:
    """Write a ``_distance`` attribute for path metrics. Returns the attr name or None
    (None means unweighted / hop count)."""
    if weighting is Weighting.NONE:
        return None
    missing = 0
    for _u, _v, attrs in graph.edges(data=True):
        dist = path_distance(attrs, weighting, amount_sat)
        attrs[_DISTANCE_ATTR] = dist
        if weighting is Weighting.CAPACITY and dist >= 1e12:
            missing += 1
    if missing:
        logger.warning("%d edges had unknown capacity and were given a large distance", missing)
    return _DISTANCE_ATTR


def _annotate_strength(graph: nx.Graph, weighting: Weighting, amount_sat: int) -> Optional[str]:
    """Write a ``_strength`` attribute for magnitude-based metrics. Returns the attr
    name or None (None means unweighted)."""
    if weighting is not Weighting.CAPACITY:
        return None
    for _u, _v, attrs in graph.edges(data=True):
        attrs[_STRENGTH_ATTR] = edge_strength(attrs, weighting, amount_sat)
    return _STRENGTH_ATTR


def _node_context(undirected: nx.Graph) -> Dict[str, Tuple[int, Optional[int]]]:
    """Per-node (channel count, total capacity_sat) for annotating results."""
    context: Dict[str, Tuple[int, Optional[int]]] = {}
    for node in undirected.nodes():
        channels = 0
        capacity = 0
        has_capacity = False
        for _u, _v, attrs in undirected.edges(node, data=True):
            channels += int(attrs.get("num_channels", 1))
            cap = attrs.get("capacity_sat")
            if isinstance(cap, int):
                capacity += cap
                has_capacity = True
        context[node] = (channels, capacity if has_capacity else None)
    return context


def _degree_scores(undirected: nx.Graph, metric: Metric, weighting: Weighting) -> Dict[str, float]:
    """Scores for DEGREE (channel count) or STRENGTH (capacity sum)."""
    use_capacity = metric is Metric.STRENGTH or weighting is Weighting.CAPACITY
    scores: Dict[str, float] = {}
    for node in undirected.nodes():
        total = 0.0
        for _u, _v, attrs in undirected.edges(node, data=True):
            if use_capacity:
                cap = attrs.get("capacity_sat")
                total += float(cap) if isinstance(cap, int) else 0.0
            else:
                total += float(attrs.get("num_channels", 1))
        scores[node] = total
    return scores


def _compute_scores(
    graph: nx.MultiDiGraph,
    metric: Metric,
    weighting: Weighting,
    amount_sat: int,
    k: Optional[int],
    seed: Optional[int],
    largest_component_only: bool,
) -> Tuple[Dict[str, float], nx.Graph]:
    """Dispatch to the right projection + networkx algorithm. Returns (scores,
    undirected_projection_for_context)."""
    undirected = to_undirected_simple(graph)

    if metric in (Metric.DEGREE, Metric.STRENGTH):
        return _degree_scores(undirected, metric, weighting), undirected

    if metric in (Metric.BETWEENNESS, Metric.CLOSENESS):
        if weighting is Weighting.FEE:
            # Fees are directional → rank on the directed simple graph.
            path_graph: nx.Graph = to_directed_simple(graph)
        else:
            path_graph = _largest_component(undirected) if largest_component_only else undirected
        weight_attr = _annotate_distance(path_graph, weighting, amount_sat)
        if metric is Metric.BETWEENNESS:
            scores = nx.betweenness_centrality(path_graph, k=k, weight=weight_attr, normalized=True, seed=seed)
        else:
            scores = nx.closeness_centrality(path_graph, distance=weight_attr)
        return scores, undirected

    if metric is Metric.PAGERANK:
        directed = to_directed_simple(graph)
        weight_attr = _annotate_strength(directed, weighting, amount_sat)
        scores = nx.pagerank(directed, weight=weight_attr)
        return scores, undirected

    if metric is Metric.EIGENVECTOR:
        weight_attr = _annotate_strength(undirected, weighting, amount_sat)
        try:
            scores = nx.eigenvector_centrality_numpy(undirected, weight=weight_attr)
        except (nx.NetworkXException, Exception):  # numpy path can fail to converge
            scores = nx.eigenvector_centrality(undirected, weight=weight_attr, max_iter=1000)
        return scores, undirected

    raise ValueError(f"Unsupported metric: {metric}")


def top_nodes_by(
    graph: nx.MultiDiGraph,
    metric: Metric,
    weight: Optional[object] = None,
    n: int = 20,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
    k: Optional[int] = None,
    seed: Optional[int] = None,
    largest_component_only: bool = True,
) -> List[NodeRank]:
    """Rank nodes of a snapshot graph by ``metric``.

    Args:
        graph: The canonical :class:`networkx.MultiDiGraph`.
        metric: Which measure to rank by (see :class:`Metric`).
        weight: ``None`` (unweighted), ``"fee"``, or ``"capacity"`` — honoured per the
            conventions in :mod:`lnhistoryclient.analysis.weights`. ``"fee"`` is only
            valid for path metrics.
        n: Number of top nodes to return.
        amount_sat: Reference payment for fee-weighted metrics.
        k: Pivot sample size for approximate betweenness (``None`` = exact all-pairs).
        seed: Seed for the betweenness pivot sampling (reproducibility).
        largest_component_only: For betweenness/closeness on the undirected topology,
            restrict to the largest connected component (nodes elsewhere score ~0).

    Returns:
        The top ``n`` :class:`NodeRank` entries, highest score first.
    """
    metric = Metric(metric)
    weighting = coerce_weighting(weight)
    if weighting is Weighting.FEE and metric not in (Metric.BETWEENNESS, Metric.CLOSENESS):
        raise ValueError(f"FEE weighting is only supported for path metrics, not {metric.value}")

    scores, undirected = _compute_scores(graph, metric, weighting, amount_sat, k, seed, largest_component_only)
    context = _node_context(undirected)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]
    results: List[NodeRank] = []
    for rank, (node_id, score) in enumerate(ranked, start=1):
        channels, capacity = context.get(node_id, (0, None))
        node_attrs = graph.nodes.get(node_id, {})
        results.append(
            NodeRank(
                rank=rank,
                node_id=node_id,
                score=float(score),
                alias=str(node_attrs.get("alias", "")),
                announced=bool(node_attrs.get("announced", False)),
                num_channels=channels,
                capacity_sat=capacity,
            )
        )
    return results
