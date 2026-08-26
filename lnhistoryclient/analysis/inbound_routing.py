"""Cheapest-route search that prices lnd's inbound fees during the search itself.

Re-pricing a route after the fact tells you how wrong its price was. It cannot tell you
whether a router that *knew* about inbound fees would have chosen a different route. That
needs the fee inside the search, and the fee is awkward:

    fee at node v = max(0, inbound_v(incoming channel) + outbound_v(outgoing channel))

It depends on a **pair** of edges, so it is not an edge weight and plain Dijkstra over
nodes cannot express it.

**Why the obvious shortcut is wrong.** Drop the ``max(0, ...)`` and the cost separates
perfectly: each edge carries ``outbound(e)`` charged at its tail and ``inbound(e)`` charged
at its head, the pairing disappears, and node-state Dijkstra works. That is tempting and it
is not sound -- measured on the 2026-07-01 snapshot the clamp binds on **13.5%** of
(incoming, outgoing) pairs at discounting nodes, and 246 of the 290 discounting nodes have
at least one pair where it binds. A discount larger than the outbound fee it pairs with is
common, not exotic.

So the search runs over **edge states**: a state is "at node ``v``, having arrived on edge
``e``", and ``v``'s fee is charged on leaving. That is the same expansion lnd carries,
reached from the other side -- lnd searches backward from the destination, fixing the
outgoing edge instead of the incoming one.

**The objective is lnd's, not raw fee.** Minimising fee alone is degenerate on this
network: 4.4% of usable directions charge exactly zero at 100k sat, so a pure fee search
collapses onto free-but-circuitous paths and inbound fees can never improve on zero. lnd's
actual weight adds a time-lock penalty::

    weight = fee_msat + amount_msat * cltv_delta * RiskFactorBillionths / 1e9

(``routing/pathfind.go``, ``RiskFactorBillionths = 15``.) The penalty is per-edge and
identical whether or not inbound fees are priced, so it breaks the tie without biasing the
comparison this module exists to support. Pass ``risk_factor=0`` for the pure-fee search.

**Amounts are priced at the payment amount, not compounded.** The amount actually crossing
an upstream channel is larger, by the downstream fees the sender pre-pays. Compounding is
second-order for *selection* and is what this package's other routers already do, so
pricing this way keeps the comparison honest. It is not good enough for reporting a route's
true cost -- use :func:`~lnhistoryclient.analysis.weights.route_fee_msat` with per-hop
amounts for that.
"""

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from lnhistoryclient.analysis.weights import fee_msat, inbound_fee_msat

#: lnd's ``RiskFactorBillionths``: the price it puts on a hop's time lock.
RISK_FACTOR_BILLIONTHS = 15

#: Sentinel for "no incoming edge yet" -- the state sitting on the sender.
_START: "EdgeKey" = ("", "", -1)

EdgeKey = Tuple[str, str, int]


@dataclass
class Route:
    """A found route: the edges in traversal order, its fee, and its search objective."""

    edges: List[EdgeKey] = field(default_factory=list)
    fee_msat: int = 0
    weight: int = 0

    @property
    def hops(self) -> int:
        return len(self.edges)

    @property
    def nodes(self) -> List[str]:
        if not self.edges:
            return []
        return [self.edges[0][0]] + [dst for _, dst, _ in self.edges]


def _usable(attrs: Dict[str, object], amount_msat: int) -> bool:
    """The notion of a usable channel the rest of the package applies.

    Kept deliberately identical so a route found here can be compared against one from
    ``analysis.routing`` without the difference being an artefact of stricter filtering.
    """
    if not attrs.get("has_update") or attrs.get("disabled"):
        return False
    htlc_min = attrs.get("htlc_minimum_msat")
    htlc_max = attrs.get("htlc_maximum_msat")
    if isinstance(htlc_min, int) and amount_msat < htlc_min:
        return False
    if isinstance(htlc_max, int) and amount_msat > htlc_max:
        return False
    capacity = attrs.get("capacity_sat")
    if isinstance(capacity, int) and amount_msat > capacity * 1000:
        return False
    return True


def _time_lock_penalty(attrs: Dict[str, object], amount_msat: int, risk_factor: int) -> int:
    if not risk_factor:
        return 0
    cltv = attrs.get("cltv_expiry_delta")
    return (amount_msat * (cltv if isinstance(cltv, int) else 0) * risk_factor) // 1_000_000_000


def _min_inbound_per_node(graph: object, amount_sat: int, inbound: bool) -> Dict[str, int]:
    """Most negative inbound fee chargeable at each node, over all its incoming channels.

    Used only to build an admissible lower bound: the true fee at a node is
    ``max(0, actual_inbound + outbound)`` and ``actual_inbound >= min_inbound``, so
    substituting the minimum can only understate the cost.
    """
    if not inbound:
        return {}
    minimum: Dict[str, int] = {}
    for _, dst, attrs in graph.edges(data=True):
        value = inbound_fee_msat(attrs, amount_sat)
        if value < minimum.get(dst, 0):
            minimum[dst] = value
    return minimum


def _heuristic(
    graph: object,
    target: str,
    amount_sat: int,
    risk_factor: int,
    min_inbound: Dict[str, int],
) -> Dict[str, int]:
    """Lower bound on the remaining cost from every node to ``target``.

    A backward Dijkstra over *nodes* using the per-edge lower bound

        l(x -> y) = max(0, outbound(x -> y) + min_inbound(x)) + time_lock_penalty

    which never exceeds the true fee charged at ``x``, because the true incoming fee at
    ``x`` is at least ``min_inbound(x)``. Admissible and consistent, so A* with a closed
    set is exact.

    It doubles as the reachability test: a node absent from the result cannot reach the
    target at all, which turns an unreachable query from a full exhaustion of the state
    space into an immediate answer.
    """
    amount_msat = amount_sat * 1000
    dist: Dict[str, int] = {target: 0}
    heap: List[Tuple[int, str]] = [(0, target)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > dist.get(node, cost):
            continue
        for predecessor, _, attrs in graph.in_edges(node, data=True):
            if not _usable(attrs, amount_msat):
                continue
            outbound = fee_msat(attrs, amount_sat)
            signed = outbound + min_inbound.get(predecessor, 0)
            step = (signed if signed > 0 else 0) + _time_lock_penalty(attrs, amount_msat, risk_factor)
            new = cost + step
            if new < dist.get(predecessor, new + 1):
                dist[predecessor] = new
                heapq.heappush(heap, (new, predecessor))
    return dist


def find_route(
    graph: object,
    source: str,
    target: str,
    amount_sat: int,
    *,
    inbound: bool = True,
    risk_factor: int = RISK_FACTOR_BILLIONTHS,
    max_hops: int = 20,
    max_expansions: int = 500_000,
) -> Optional[Route]:
    """Cheapest route under lnd's weight, optionally pricing inbound fees.

    Args:
        graph: A ``MultiDiGraph`` from
            :func:`~lnhistoryclient.graph.builder.build_multidigraph`, built with
            ``inbound_fees=True`` -- otherwise the ``dst_inbound_fee_*`` attributes are
            absent and ``inbound=True`` degrades silently to ``inbound=False``.
        source: Sender's node id.
        target: Recipient's node id.
        amount_sat: Amount to deliver.
        inbound: When False, price outbound fees only -- the pre-TLV model, and the
            baseline this exists to be compared against.
        risk_factor: lnd's ``RiskFactorBillionths``. Pass 0 to minimise fee alone.
        max_hops: Abandon paths longer than this.
        max_expansions: Give up and return ``None`` rather than hang on a pathological
            query. A few 2000-channel hubs put the worst-case state space near 19M pairs.

    Returns:
        The cheapest :class:`Route`, or ``None`` when the target is unreachable under the
        channel constraints.

    **Loop-free, and that costs the optimality guarantee.** Because a node's fee depends on
    the channel the payment arrived by, a path that revisits a node can be genuinely
    cheaper -- looping lets the search re-enter on a channel carrying a larger discount.
    Such a path is not a usable route, so revisits are pruned; but shortest-path search
    over *simple* paths with pair-dependent costs is NP-hard in general, so this is
    Dijkstra with revisit-pruning rather than a proof of optimality. Checked empirically
    against exhaustive simple-path enumeration on random graphs in the test suite, where
    it agrees exactly.

    The sender charges nothing on the first hop and the recipient charges no inbound fee
    on the last, so fees accrue only at intermediate nodes -- which is why a node's fee is
    booked when *leaving* it rather than when arriving.
    """
    amount_msat = amount_sat * 1000
    if source not in graph or target not in graph or source == target:
        return None

    min_inbound = _min_inbound_per_node(graph, amount_sat, inbound)
    remaining = _heuristic(graph, target, amount_sat, risk_factor, min_inbound)
    if source not in remaining:
        return None  # provably unreachable; no search needed

    counter = 0
    start = (source, _START)
    # (f = g + h, counter, g, fee, node, incoming edge, hops)
    heap: List[Tuple[int, int, int, int, str, EdgeKey, int]] = [
        (remaining[source], counter, 0, 0, source, _START, 0)
    ]
    best: Dict[Tuple[str, EdgeKey], int] = {start: 0}
    parent: Dict[Tuple[str, EdgeKey], Tuple[str, EdgeKey]] = {}
    expansions = 0

    while heap:
        _, _, cost, fee, node, incoming, hops = heapq.heappop(heap)
        state = (node, incoming)
        if cost > best.get(state, cost):
            continue
        if node == target:
            edges: List[EdgeKey] = []
            cursor = state
            while cursor in parent:
                edges.append(cursor[1])
                cursor = parent[cursor]
            edges.reverse()
            return Route(edges=edges, fee_msat=fee, weight=cost)
        if hops >= max_hops:
            continue
        expansions += 1
        if expansions > max_expansions:
            return None

        # Nodes already on this path. A route that revisits a node is not a route, and
        # with pair-dependent costs the search will actively seek one out: looping back
        # through a node lets it "re-enter" on a channel carrying a bigger discount and
        # make the next hop free. Left unchecked this produces strictly-cheaper answers
        # that no client could ever use.
        visited = {node}
        cursor = state
        while cursor in parent:
            cursor = parent[cursor]
            visited.add(cursor[0])

        # A node's fee is fixed only once its outgoing channel is chosen, so it is booked
        # here, on the way out.
        inbound_component = 0
        if inbound and incoming != _START:
            incoming_attrs = graph.get_edge_data(*incoming)
            if incoming_attrs is not None:
                inbound_component = inbound_fee_msat(incoming_attrs, amount_sat)

        for _, neighbour, key, attrs in graph.out_edges(node, keys=True, data=True):
            if neighbour in visited or neighbour not in remaining:
                continue
            if not _usable(attrs, amount_msat):
                continue
            if node == source:
                step_fee = 0  # the sender does not charge itself
            else:
                signed = inbound_component + fee_msat(attrs, amount_sat)
                step_fee = signed if signed > 0 else 0
            step = step_fee + _time_lock_penalty(attrs, amount_msat, risk_factor)
            edge_key: EdgeKey = (node, neighbour, key)
            next_state = (neighbour, edge_key)
            next_cost = cost + step
            if next_cost < best.get(next_state, next_cost + 1):
                best[next_state] = next_cost
                parent[next_state] = state
                counter += 1
                heapq.heappush(
                    heap,
                    (
                        next_cost + remaining[neighbour],
                        counter,
                        next_cost,
                        fee + step_fee,
                        neighbour,
                        edge_key,
                        hops + 1,
                    ),
                )

    return None
