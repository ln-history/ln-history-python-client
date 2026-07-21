"""Topology statistics for a Lightning Network snapshot graph.

Ported from the former ``overnight_analysis.compute_graph_stats`` and adapted to the
canonical :class:`networkx.MultiDiGraph`. Returns a plain dict suitable for JSON.
"""

from typing import Dict, Optional

import networkx as nx

from lnhistoryclient.graph.projections import to_undirected_simple


def graph_stats(graph: nx.MultiDiGraph, label: Optional[str] = None) -> Dict[str, object]:
    """Collect topology statistics for a snapshot graph.

    Args:
        graph: The canonical ``MultiDiGraph``.
        label: Optional identifier (e.g. an ISO date) echoed back under ``"label"``.

    Returns:
        A dict of counts and connectivity metrics. Channel/edge counts refer to
        distinct channels (``scid``), not the doubled directed edges.
    """
    undirected = to_undirected_simple(graph)
    components = sorted(nx.connected_components(undirected), key=len, reverse=True)

    announced = sum(1 for _, a in graph.nodes(data=True) if a.get("announced"))
    total_nodes = graph.number_of_nodes()
    isolated = sum(1 for n in graph.nodes() if graph.degree(n) == 0)

    distinct_channels = len({key for _u, _v, key in graph.edges(keys=True)})
    directions_with_update = sum(1 for *_r, a in graph.edges(data=True) if a.get("has_update"))
    directions_disabled = sum(1 for *_r, a in graph.edges(data=True) if a.get("disabled"))

    largest = len(components[0]) if components else 0
    return {
        "label": label,
        "nodes_total": total_nodes,
        "nodes_announced": announced,
        "nodes_unannounced": total_nodes - announced,
        "nodes_isolated": isolated,
        "directed_edges": graph.number_of_edges(),
        "channels": distinct_channels,
        "directions_with_update": directions_with_update,
        "directions_disabled": directions_disabled,
        "connected_components": len(components),
        "largest_component_nodes": largest,
        "largest_component_pct": round(largest / total_nodes * 100, 2) if total_nodes else 0.0,
    }
