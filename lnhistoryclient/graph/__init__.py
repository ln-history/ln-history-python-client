"""Graph construction and projection for Lightning Network snapshots.

The canonical representation is a lossless :class:`networkx.MultiDiGraph` built by
:func:`build_multidigraph`. It preserves every channel (including parallel channels
between the same pair of nodes) as two directed edges keyed by ``scid``. Analytics
code should not consume the ``MultiDiGraph`` directly for path-based metrics; instead
it should collapse it with one of the projection helpers:

* :func:`to_undirected_simple` — a simple ``Graph`` for topological centrality.
* :func:`to_directed_simple` — a simple ``DiGraph`` for weighted routing / fees.

Capacity is not carried by BOLT #7 ``channel_announcement`` messages. Use
:func:`attach_capacity` to enrich the graph with real ``capacity_sat`` values fetched
separately, or rely on the ``htlc_maximum_msat`` proxy that the builder records.
"""

from lnhistoryclient.graph.builder import build_multidigraph
from lnhistoryclient.graph.enrich import attach_capacity
from lnhistoryclient.graph.projections import to_directed_simple, to_undirected_simple
from lnhistoryclient.graph.stats import graph_stats

__all__ = [
    "build_multidigraph",
    "attach_capacity",
    "to_directed_simple",
    "to_undirected_simple",
    "graph_stats",
]
