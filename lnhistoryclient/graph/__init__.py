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

Channel *balances* are not in gossip either. :func:`assign_balances` populates them from
a user-chosen distribution so payments can be simulated against a stated liquidity
assumption; see :mod:`lnhistoryclient.graph.balances` for what that assumption does and
does not mean.
"""

from lnhistoryclient.graph.balances import (
    Balanced,
    BalanceDistribution,
    BalanceScenario,
    Beta,
    Custom,
    Normal,
    Orientation,
    Polarised,
    Uniform,
    apply_balance_delta,
    assign_balances,
    load_balances,
    save_balances,
    set_balance,
)
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
    # balances
    "assign_balances",
    "set_balance",
    "apply_balance_delta",
    "save_balances",
    "load_balances",
    "BalanceScenario",
    "Orientation",
    "BalanceDistribution",
    "Balanced",
    "Uniform",
    "Beta",
    "Normal",
    "Polarised",
    "Custom",
]
