"""Opt-in enrichment of the canonical graph with on-chain channel capacity.

BOLT #7 ``channel_announcement`` messages carry no capacity, so a snapshot-only graph
falls back to the ``htlc_maximum_msat`` proxy. When exact capacity is wanted, fetch a
``scid -> capacity_sat`` mapping (e.g. from the ln-history API's bulk capacity
endpoint) and call :func:`attach_capacity` to write it onto every matching edge.
"""

import logging
from typing import Dict

import networkx as nx

logger = logging.getLogger(__name__)


def attach_capacity(graph: nx.MultiDiGraph, scid_to_capacity_sat: Dict[int, int]) -> nx.MultiDiGraph:
    """Write ``capacity_sat`` onto every directed edge whose ``scid`` is in the map.

    Args:
        graph: The canonical ``MultiDiGraph`` (mutated in place).
        scid_to_capacity_sat: Mapping of integer ``scid`` to on-chain capacity in sat.

    Returns:
        The same graph, for chaining. Edges whose ``scid`` is absent from the map keep
        their proxy-based capacity (``capacity_sat`` stays ``None``).
    """
    matched = 0
    for _src, _dst, key, attrs in graph.edges(keys=True, data=True):
        scid = key if isinstance(key, int) else attrs.get("scid")
        capacity = scid_to_capacity_sat.get(scid)
        if capacity is not None:
            attrs["capacity_sat"] = capacity
            matched += 1

    logger.info(
        "attach_capacity: set capacity_sat on %d/%d directed edges from %d scid entries",
        matched,
        graph.number_of_edges(),
        len(scid_to_capacity_sat),
    )
    return graph
