"""Pathfinding over a snapshot graph, behind a pluggable strategy seam.

:class:`RoutingStrategy` is the extension point: v1 ships :class:`CheapestFeeRouter`
(single-path Dijkstra minimising forwarding fees), and multi-part / probabilistic
routers can be added later without changing the payment-simulation API.

Hard constraints (a channel is *excluded* before routing) are applied by
:func:`routable_view`: a direction is unusable if it carries no ``channel_update``, if
it is disabled, if the amount is below ``htlc_minimum_msat`` or above
``htlc_maximum_msat``, or if it exceeds the channel's capacity.

A direction with no ``channel_update`` has **no known policy**, and BOLT #7 requires an
update before a direction may be used for routing. Treating it as free (the pre-4.1.0
behaviour) was actively harmful: fee-minimising Dijkstra then *preferred* precisely the
channels whose cost was unknown. On a 2021 snapshot that was 31% of all directions.

**Balances are unknowable from gossip.** A returned route means a path *could* exist
and what it *would* cost — it is an upper bound on routability, not a guarantee that
liquidity sits on the right side of each channel.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

import networkx as nx

from lnhistoryclient.analysis.weights import fee_msat

logger = logging.getLogger(__name__)

_FEE_ATTR = "_fee_msat"


@dataclass
class Route:
    """A single computed path from source to destination."""

    path: List[str]
    hops: int
    total_fee_msat: int
    total_cltv: int
    amount_sat: int


def routable_view(directed_simple: nx.DiGraph, amount_sat: int) -> nx.DiGraph:
    """Return a subgraph of edges that can carry ``amount_sat``, with a ``_fee_msat``
    weight attached to each surviving edge.

    Excludes directions with no ``channel_update`` (unknown policy), disabled
    directions, and edges whose htlc/capacity bounds cannot carry the amount.
    """
    amount_msat = amount_sat * 1000
    view = nx.DiGraph()
    view.add_nodes_from(directed_simple.nodes(data=True))
    for src, dst, attrs in directed_simple.edges(data=True):
        # No channel_update means no advertised policy: not routable, not free.
        if not attrs.get("has_update"):
            continue
        if attrs.get("disabled"):
            continue
        htlc_min = attrs.get("htlc_minimum_msat")
        if isinstance(htlc_min, int) and amount_msat < htlc_min:
            continue
        htlc_max = attrs.get("htlc_maximum_msat")
        if isinstance(htlc_max, int) and amount_msat > htlc_max:
            continue
        capacity = attrs.get("capacity_sat")
        if isinstance(capacity, int) and amount_sat > capacity:
            continue
        edge_attrs = dict(attrs)
        edge_attrs[_FEE_ATTR] = fee_msat(attrs, amount_sat)
        view.add_edge(src, dst, **edge_attrs)
    return view


class RoutingStrategy(ABC):
    """Strategy seam for pathfinding. Implementations return a route or ``None``."""

    @abstractmethod
    def find_route(self, routable: nx.DiGraph, src: str, dst: str, amount_sat: int) -> Optional[Route]:
        """Find a route for ``amount_sat`` from ``src`` to ``dst`` in a routable view."""


class CheapestFeeRouter(RoutingStrategy):
    """Single-path router minimising total forwarding fee (Dijkstra on ``_fee_msat``).

    Fee/cltv accounting follows Lightning convention: the policy on a directed edge
    ``u -> v`` is charged by ``u`` for *forwarding* onward. The sender originates the
    payment rather than forwarding it, so it never charges itself on its own outgoing
    channel — the **first** edge's fee and cltv delta are excluded, and every
    subsequent edge's are included. A direct ``src -> dst`` payment therefore costs
    nothing: there is no forwarder.

    The per-edge fee is computed for the base ``amount_sat`` (the amount-grows-upstream
    effect is not modelled here).
    """

    def find_route(self, routable: nx.DiGraph, src: str, dst: str, amount_sat: int) -> Optional[Route]:
        if src not in routable or dst not in routable:
            return None

        def _weight(u: str, _v: str, attrs: Dict[str, object]) -> float:
            """Edge cost for pathfinding: the sender pays nothing on its own channel."""
            return 0.0 if u == src else float(attrs[_FEE_ATTR])  # type: ignore[arg-type]

        try:
            path = nx.dijkstra_path(routable, src, dst, weight=_weight)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

        edges = list(zip(path[:-1], path[1:], strict=False))
        fees = [int(routable[u][v][_FEE_ATTR]) for u, v in edges]
        cltvs = [int(routable[u][v].get("cltv_expiry_delta") or 0) for u, v in edges]
        # Drop the first edge: its policy belongs to the sender, who does not forward.
        total_fee = sum(fees[1:])
        return Route(
            path=path,
            hops=len(edges),
            total_fee_msat=total_fee,
            total_cltv=sum(cltvs[1:]),
            amount_sat=amount_sat,
        )
