"""Pathfinding over a snapshot graph, behind a pluggable strategy seam.

:class:`RoutingStrategy` is the extension point; v1 ships
:class:`ReverseDijkstraRouter`. Multi-part (MPP) and probabilistic routers can be added
here without changing the payment-simulation API.

Why the search runs *backwards*
-------------------------------
A Lightning fee depends on the amount being forwarded, and the amount **grows upstream**:
the first hop must carry the payment *plus every downstream fee*. A forward search
therefore cannot know an edge's true cost when it relaxes it, and any ``htlc_maximum``,
capacity or balance test it applies is against an amount that is too small.

Rooting Dijkstra at the **destination** fixes this. The label at a node is "the amount
that must arrive here for ``amount_sat`` to reach the recipient", so every relaxation
knows the exact amount crossing that channel. Minimising the label at the source is
exactly minimising total fee. This is sound for Dijkstra: fees are non-negative and the
extension ``amount -> amount + base + amount*ppm/1e6`` is monotonically increasing in the
label, so settled labels are final. LND, CLN and eclair all search this direction.

Two consequences worth knowing:

* **The sender charges no fee on its own channel.** The policy on edge ``u -> v`` is
  charged by ``u`` for *forwarding*; the sender originates instead. A direct
  ``src -> dst`` payment therefore costs zero. The same applies to ``cltv_expiry_delta``.
* **``htlc_minimum_msat`` is a lower bound**, so in principle a route feasible only at a
  *larger* amount can be missed, since Dijkstra settles each node at its minimum. This is
  a known and accepted gap in real implementations too.

Parallel channels
-----------------
The search walks the canonical ``MultiDiGraph`` rather than a simple projection, because
collapsing parallel channels breaks single-path routing in two ways: summing their
balances implies a payment can be split across them (it cannot — one HTLC rides one
channel), and picking the cheapest policy discards a pricier sibling that may hold the
only usable liquidity. Each parallel is evaluated separately and the route records the
``scid`` actually used.

Hard constraints are selected per call via :class:`Constraint`, which is what lets the
payment simulator attribute a failure by re-running with tiers progressively dropped.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from enum import IntFlag
from heapq import heappop, heappush
from typing import Any, Dict, Final, FrozenSet, Iterable, List, Optional, Set, Tuple

import networkx as nx

from lnhistoryclient.analysis.clients.base import (
    Algorithm,
    ClientProfile,
    EdgeContext,
    NetworkStats,
    PathConstraints,
)
from lnhistoryclient.analysis.weights import DEFAULT_AMOUNT_SAT
from lnhistoryclient.graph.balances import LOCAL_BALANCE_ATTR
from lnhistoryclient.graph.projections import edge_capacity_sat

logger = logging.getLogger(__name__)

# Stand-in for "no limit" in the hot loop, so bounds need no None checks per relaxation.
_UNBOUNDED = 1 << 62


class Constraint(IntFlag):
    """Hard constraints the router enforces. Combine with ``|``; drop tiers to diagnose."""

    NONE = 0
    #: Require an advertised ``channel_update``; without one the policy is unknown.
    POLICY = 1
    #: Respect the ``disabled`` bit in ``channel_flags``.
    DISABLED = 2
    #: Respect ``htlc_minimum_msat`` / ``htlc_maximum_msat``.
    HTLC_BOUNDS = 4
    #: Respect the channel's on-chain capacity.
    CAPACITY = 8
    #: Respect the assigned local balance (requires :func:`~...balances.assign_balances`).
    BALANCE = 16
    ALL = POLICY | DISABLED | HTLC_BOUNDS | CAPACITY | BALANCE


#: Everything a snapshot can actually assert about a channel. ``BALANCE`` is excluded
#: because gossip never carries it, so it fails closed unless balances were assigned.
_STRUCTURAL: Final = Constraint.POLICY | Constraint.DISABLED | Constraint.HTLC_BOUNDS | Constraint.CAPACITY


@dataclass
class Hop:
    """One channel traversal in a route, with the exact amount that crosses it."""

    src: str
    dst: str
    scid: int
    direction: Optional[int]
    #: HTLC value on this channel — what ``dst`` receives. Grows toward the sender.
    amount_forwarded_msat: int
    #: Fee charged *by* ``src`` for forwarding onward. Zero for the sender's own hop.
    fee_msat: int
    cltv_expiry_delta: int
    htlc_minimum_msat: Optional[int]
    htlc_maximum_msat: Optional[int]
    capacity_sat: Optional[int]
    balance_before_sat: Optional[int]
    balance_after_sat: Optional[int]

    def to_dict(self) -> Dict[str, object]:
        return {
            "src": self.src,
            "dst": self.dst,
            "scid": self.scid,
            "direction": self.direction,
            "amount_forwarded_msat": self.amount_forwarded_msat,
            "fee_msat": self.fee_msat,
            "cltv_expiry_delta": self.cltv_expiry_delta,
            "htlc_minimum_msat": self.htlc_minimum_msat,
            "htlc_maximum_msat": self.htlc_maximum_msat,
            "capacity_sat": self.capacity_sat,
            "balance_before_sat": self.balance_before_sat,
            "balance_after_sat": self.balance_after_sat,
        }


@dataclass
class Route:
    """A single computed path from source to destination.

    ``hops`` carries one :class:`Hop` per channel traversed, in send order.
    ``total_fee_msat`` is the sum of every *forwarder's* fee — the sender's own policy is
    excluded, so a direct payment costs nothing.
    """

    path: List[str]
    hops: List[Hop] = field(default_factory=list)
    total_fee_msat: int = 0
    total_cltv: int = 0
    amount_sat: int = 0
    #: Total cost under the client's weight function. Meaningless (and left at 0) for the
    #: fee-optimal router, whose cost *is* the fee.
    total_weight: float = 0.0
    #: Estimated probability the whole path can forward, i.e. ``prod P_e``. Stays 1.0 for
    #: clients that do not model channel success probability.
    success_probability: float = 1.0
    #: ``ClientProfile.label`` of the client that produced this route, when applicable.
    client: Optional[str] = None

    @property
    def num_hops(self) -> int:
        """Number of channels traversed."""
        return len(self.hops)

    @property
    def amount_sent_msat(self) -> int:
        """What the sender must actually part with: the amount plus all forwarding fees."""
        return self.amount_sat * 1000 + self.total_fee_msat


# Index record layout. A tuple rather than a dataclass because it is unpacked once per
# edge relaxation, and the attrs reference is kept so balances are read *live* — a
# committed payment is visible to the next search without rebuilding the index.
#: src, scid, has_update, disabled, fee_base, ppm, cltv, htlc_min, htlc_max, capacity_msat,
#: attrs, funding_block, effective_capacity_msat
_Record = Tuple[str, int, bool, bool, int, int, int, int, int, int, Dict[str, Any], int, Optional[int]]

# Field offsets. Declared Final so they are literal types: indexing a heterogeneous
# tuple with a plain int variable widens to a union of every field type.
_SRC: Final = 0
_SCID: Final = 1
_HAS_UPDATE: Final = 2
_DISABLED: Final = 3
_BASE: Final = 4
_PPM: Final = 5
_CLTV: Final = 6
_HMIN: Final = 7
_HMAX: Final = 8
_CAP_MSAT: Final = 9
_ATTRS: Final = 10
# Fields below are read only by client weight functions, never by the hard-constraint
# filters, and are appended so the offsets above stay stable.
_BLOCK: Final = 11
_CAP_EFF: Final = 12


class RoutingIndex:
    """Reverse adjacency over the canonical ``MultiDiGraph``, built once and reused.

    Only *amount-independent* structure is precomputed; every amount-dependent test
    happens during relaxation, where the true per-hop amount is known. Balances are read
    live from the edge attributes, so settlement needs no rebuild.
    """

    def __init__(self, graph: nx.MultiDiGraph) -> None:
        self.graph = graph
        self.predecessors: Dict[str, List[_Record]] = {}
        edge_count = 0
        blocks: List[int] = []

        for src, dst, key, attrs in graph.edges(keys=True, data=True):
            scid = key if isinstance(key, int) else attrs.get("scid")
            htlc_min = attrs.get("htlc_minimum_msat")
            htlc_max = attrs.get("htlc_maximum_msat")
            capacity = attrs.get("capacity_sat")
            # BOLT #7 packs the funding block into the scid's top 24 bits. Client weight
            # functions that price channel age (eclair) read it from here.
            funding_block = (scid >> 40) & 0xFFFFFF if isinstance(scid, int) else 0
            if funding_block:
                blocks.append(funding_block)
            # Effective capacity falls back to the htlc_maximum proxy, because on an
            # un-enriched snapshot most channels have no on-chain capacity and a weight
            # function that needs one would otherwise see nothing. The *constraint* at
            # _CAP_MSAT deliberately does not do this: a proxy must not gate feasibility.
            effective = edge_capacity_sat(attrs)
            self.predecessors.setdefault(dst, []).append(
                (
                    src,
                    scid,
                    bool(attrs.get("has_update")),
                    bool(attrs.get("disabled")),
                    int(attrs.get("fee_base_msat") or 0),
                    int(attrs.get("fee_proportional_millionths") or 0),
                    int(attrs.get("cltv_expiry_delta") or 0),
                    int(htlc_min) if isinstance(htlc_min, int) else 0,
                    int(htlc_max) if isinstance(htlc_max, int) else _UNBOUNDED,
                    int(capacity) * 1000 if isinstance(capacity, int) else _UNBOUNDED,
                    attrs,
                    funding_block,
                    effective * 1000 if effective else None,
                )
            )
            edge_count += 1

        self.nodes = set(graph.nodes())
        #: Chain-tip proxy and oldest channel, for weight functions that price age.
        self.stats = NetworkStats(
            tip_block_height=max(blocks) if blocks else 0,
            min_funding_block=min(blocks) if blocks else 0,
        )
        logger.info("RoutingIndex: %d nodes, %d directed edges", len(self.nodes), edge_count)

    def __contains__(self, node: str) -> bool:
        return node in self.nodes

    def incoming(self, node: str) -> Iterable[_Record]:
        """Records for every directed edge pointing *into* ``node``."""
        return self.predecessors.get(node, ())

    def routable_core(
        self,
        amount_sat: int = DEFAULT_AMOUNT_SAT,
        constraints: Constraint = _STRUCTURAL,
    ) -> Set[str]:
        """The largest set of nodes that can all pay each other at ``amount_sat``.

        Formally the largest strongly connected component of the subgraph of directions
        that pass ``constraints``. Every ordered pair drawn from it has *some* route, so a
        trial set restricted to it isolates **which** route a client picks from **whether**
        one exists.

        That distinction matters more than it sounds. On an archived snapshot most nodes
        sit outside this component — usually because the archive returned no
        ``channel_update`` for their channels rather than because the real network was
        partitioned — and a uniform draw over all nodes then spends most of its trials
        re-measuring archive coverage. Sampling the core removes that, at the cost of a
        deliberately non-representative pair distribution: report the unrestricted success
        rate alongside anything measured on the core.

        The default ``constraints`` deliberately omit :attr:`Constraint.BALANCE`, which
        fails closed on a graph with no balances assigned, and are evaluated at a single
        amount — a channel is in or out for *this* payment size.
        """
        amount_msat = amount_sat * 1000
        reachable = nx.DiGraph()
        reachable.add_nodes_from(self.nodes)
        for dst, records in self.predecessors.items():
            for record in records:
                if _edge_passes(record, amount_msat, constraints):
                    reachable.add_edge(record[_SRC], dst)
        # Ties broken on the smallest member, not left to set iteration order: callers
        # derive trial sets from this, and a pool that shifts between processes would
        # silently unpair a comparison that only means anything while it stays paired.
        components = list(nx.strongly_connected_components(reachable))
        if not components:
            return set()
        largest = max(components, key=lambda part: (len(part), min(part)))
        # Every node is trivially strongly connected to itself, so a singleton always
        # wins on a graph with no usable edges. It is not a core: nobody can pay anybody.
        return set(largest) if len(largest) > 1 else set()


class RoutingStrategy(ABC):
    """Strategy seam for pathfinding. Implementations return a route or ``None``."""

    @abstractmethod
    def find_route(
        self,
        index: RoutingIndex,
        src: str,
        dst: str,
        amount_sat: int,
        constraints: Constraint = Constraint.ALL,
    ) -> Optional[Route]:
        """Find a route for ``amount_sat`` from ``src`` to ``dst``."""

    def without_side_constraints(self) -> Optional["RoutingStrategy"]:
        """A copy of this strategy with its *client-level* limits lifted, or ``None``.

        The fee-optimal router has no such limits, so it returns ``None``. A
        :class:`ClientRouter` does — a fee budget, a CLTV ceiling, a minimum path
        probability — and a payment can fail on those while the network route exists.
        Failure attribution uses this to tell "no path" apart from "this client refused
        the path", which are very different findings.
        """
        return None


class ReverseDijkstraRouter(RoutingStrategy):
    """Single-path cheapest-fee router, searching backwards from the destination.

    See the module docstring for why the search runs in this direction and what the
    sender-exemption and ``htlc_minimum`` caveats mean.
    """

    def find_route(
        self,
        index: RoutingIndex,
        src: str,
        dst: str,
        amount_sat: int,
        constraints: Constraint = Constraint.ALL,
    ) -> Optional[Route]:
        if src == dst or src not in index or dst not in index:
            return None

        amount_msat = amount_sat * 1000

        # label[n] = amount that must arrive at n for amount_msat to reach dst.
        label: Dict[str, int] = {dst: amount_msat}
        successor: Dict[str, Tuple[str, _Record]] = {}
        settled: Set[str] = set()
        heap: List[Tuple[int, str]] = [(amount_msat, dst)]

        while heap:
            amount_at_v, node_v = heappop(heap)
            if node_v in settled:
                continue
            settled.add(node_v)
            if node_v == src:
                break

            for record in index.incoming(node_v):
                node_u = record[_SRC]
                if node_u in settled:
                    continue
                if not _edge_passes(record, amount_at_v, constraints):
                    continue

                # The sender originates rather than forwards, so it charges nothing.
                if node_u == src:
                    amount_at_u = amount_at_v
                else:
                    amount_at_u = amount_at_v + record[_BASE] + (amount_at_v * record[_PPM]) // 1_000_000

                if amount_at_u < label.get(node_u, _UNBOUNDED):
                    label[node_u] = amount_at_u
                    successor[node_u] = (node_v, record)
                    heappush(heap, (amount_at_u, node_u))

        if src not in label:
            return None
        return _build_route(label, successor, src, dst, amount_sat)


def _build_route(
    label: Dict[str, int],
    successor: Dict[str, Tuple[str, _Record]],
    src: str,
    dst: str,
    amount_sat: int,
) -> Optional[Route]:
    """Walk the successor chain from source to destination, materialising hops.

    ``label`` maps each node to the amount that must arrive there, which every reverse
    search maintains regardless of what it minimises — so both the fee-optimal router and
    the client routers share this.
    """
    path = [src]
    hops: List[Hop] = []
    node = src
    # The chain is acyclic by construction; the bound is a cheap guard against a
    # malformed index rather than an expected condition.
    for _ in range(len(label) + 1):
        if node == dst:
            break
        step = successor.get(node)
        if step is None:
            return None
        node_v, record = step
        amount_forwarded = label[node_v]
        local = record[_ATTRS].get(LOCAL_BALANCE_ATTR)
        # Debit rounds up: hop amounts have msat granularity, balances are in sat, and
        # rounding down would slowly inflate liquidity across a sequential run.
        debit_sat = -(-amount_forwarded // 1000)
        hops.append(
            Hop(
                src=node,
                dst=node_v,
                scid=record[_SCID],
                direction=record[_ATTRS].get("direction"),
                amount_forwarded_msat=amount_forwarded,
                fee_msat=label[node] - amount_forwarded,
                cltv_expiry_delta=record[_CLTV],
                htlc_minimum_msat=record[_ATTRS].get("htlc_minimum_msat"),
                htlc_maximum_msat=record[_ATTRS].get("htlc_maximum_msat"),
                capacity_sat=record[_ATTRS].get("capacity_sat"),
                balance_before_sat=local,
                balance_after_sat=None if local is None else local - debit_sat,
            )
        )
        path.append(node_v)
        node = node_v
    else:
        return None

    return Route(
        path=path,
        hops=hops,
        total_fee_msat=label[src] - amount_sat * 1000,
        # The first hop's delta belongs to the sender, who does not forward.
        total_cltv=sum(hop.cltv_expiry_delta for hop in hops[1:]),
        amount_sat=amount_sat,
    )


def _edge_passes(record: _Record, amount_msat: int, constraints: Constraint) -> bool:
    """Hard feasibility of one directed channel for ``amount_msat`` crossing it.

    Every bound is tested against the amount that actually crosses *this* channel, which
    the reverse search knows exactly. Shared by every router so the notion of "usable
    channel" cannot drift between them; the client weight functions layer *soft*
    preferences on top of this, never replacing it.
    """
    if (constraints & Constraint.POLICY) and not record[_HAS_UPDATE]:
        return False
    if (constraints & Constraint.DISABLED) and record[_DISABLED]:
        return False
    if (constraints & Constraint.HTLC_BOUNDS) and not (record[_HMIN] <= amount_msat <= record[_HMAX]):
        return False
    if (constraints & Constraint.CAPACITY) and amount_msat > record[_CAP_MSAT]:
        return False
    if constraints & Constraint.BALANCE:
        local = record[_ATTRS].get(LOCAL_BALANCE_ATTR)
        if local is None or amount_msat > local * 1000:
            return False
    return True


#: Probability floor. Clients divide by the path probability, and LND guards the same way;
#: clamping keeps a hopeless edge finite so the min-probability constraint can reject it
#: explicitly rather than the search dying on a division.
_MIN_PROBABILITY: Final = 1e-12

#: Per-node search state: amount to receive, additive weight, probability, cltv, hops,
#: path htlc-minimum (LDK's accumulator; 0 for every other client).
_Label = Tuple[int, float, float, int, int, int]

#: One hop identified independently of any particular route: source, target, channel.
_HopSpec = Tuple[str, str, int]


class ClientRouter(RoutingStrategy):
    """Route the way a specific node implementation would, per :class:`ClientProfile`.

    Wraps the three search variants of Section V of the paper behind one strategy:

    * :attr:`~...clients.base.Algorithm.DIJKSTRA` — Algorithm 1, optionally with the
      side constraints enforced during relaxation (the "colored" modification).
    * :attr:`~...clients.base.Algorithm.MODIFIED_DIJKSTRA` — Algorithm 2, whose priority
      is ``additive + attempt_cost / path_probability``. Selected implicitly whenever the
      weight function reports a non-zero attempt cost, so the two share one loop.
    * :attr:`~...clients.base.Algorithm.YEN` — K candidate paths, as eclair does.

    What this reproduces is the client's *decision rule*, not its full stack. There is no
    payment history, no learned liquidity bounds, no retry loop and no MPP splitting, so
    every probability estimate is the client's cold-start estimate. See
    :meth:`~...clients.base.WeightFunction.describe` output and ``ClientProfile.notes``
    for the per-client caveats.
    """

    def __init__(self, profile: ClientProfile) -> None:
        self.profile = profile

    def __repr__(self) -> str:
        return f"ClientRouter({self.profile.label})"

    def without_side_constraints(self) -> Optional["RoutingStrategy"]:
        """The same client's weight function with every side constraint dropped."""
        return ClientRouter(replace(self.profile, constraints=PathConstraints(), relaxed_constraints=None))

    def find_route(
        self,
        index: RoutingIndex,
        src: str,
        dst: str,
        amount_sat: int,
        constraints: Constraint = Constraint.ALL,
    ) -> Optional[Route]:
        """The single route this client would choose, or ``None``."""
        routes = self.find_routes(index, src, dst, amount_sat, constraints)
        return routes[0] if routes else None

    def find_routes(
        self,
        index: RoutingIndex,
        src: str,
        dst: str,
        amount_sat: int,
        constraints: Constraint = Constraint.ALL,
    ) -> List[Route]:
        """Every candidate this client would consider, best first.

        One route for the Dijkstra-based clients; up to ``profile.k_paths`` for eclair,
        which really does hold several and choose among them.
        """
        if amount_sat <= 0 or src == dst or src not in index or dst not in index:
            return []

        self.profile.weight.prepare(index.stats, amount_sat * 1000)

        # A client that widens its bounds after a failed first pass gets two passes here,
        # in its own order. Returning early on the first success is what makes the tighter
        # envelope observable at all: it is the route the client prefers, not merely one it
        # would tolerate.
        passes = [self.profile.constraints]
        if self.profile.relaxed_constraints is not None:
            passes.append(self.profile.relaxed_constraints)

        for limits in passes:
            found = self._search(index, src, dst, amount_sat, constraints, limits)
            if found:
                return found
        return []

    def _search(
        self,
        index: RoutingIndex,
        src: str,
        dst: str,
        amount_sat: int,
        constraints: Constraint,
        limits: PathConstraints,
    ) -> List[Route]:
        """One pass at a fixed set of side constraints."""
        best = self._dijkstra(index, src, dst, amount_sat, constraints, limits, frozenset(), frozenset())
        if self.profile.algorithm is not Algorithm.YEN or self.profile.k_paths <= 1:
            return [] if best is None or self._rejected(best, amount_sat, limits) else [best]
        return self._yen(index, src, dst, amount_sat, constraints, limits, best)

    # ── search ───────────────────────────────────────────────────────────────────

    def _dijkstra(
        self,
        index: RoutingIndex,
        src: str,
        dst: str,
        amount_sat: int,
        constraints: Constraint,
        limits: PathConstraints,
        banned_nodes: FrozenSet[str],
        banned_edges: FrozenSet[_HopSpec],
    ) -> Optional[Route]:
        """Reverse Dijkstra keyed on the client's cost rather than on the amount.

        The amount at each node is still tracked exactly — it is what every fee, bound and
        weight is evaluated against — but it is no longer what the queue minimises. That
        is the whole difference between "cheapest route" and "the route this client picks".
        """
        amount_msat = amount_sat * 1000
        weight_fn = self.profile.weight

        # Bounds bind during relaxation only for clients that check them there; the others
        # validate the finished path in _rejected(). Both are real behaviours — see
        # PathConstraints.enforce_during_search.
        prune = limits.enforce_during_search
        max_cltv = limits.max_cltv_expiry_delta if prune else None
        min_prob = limits.min_path_probability if prune else None
        max_len = limits.max_path_length if prune else None
        fee_limit = limits.fee_limit_msat(amount_msat) if prune else None

        # A scalar for the whole payment, not a per-edge term. Zero for every client but
        # LND, which collapses the priority below to the plain additive cost.
        attempt_cost = weight_fn.attempt_cost_msat(amount_msat)
        origin_cost = weight_fn.path_cost(0.0, 0, 0, 1.0, attempt_cost)

        label: Dict[str, _Label] = {dst: (amount_msat, 0.0, 1.0, 0, 0, 0)}
        dist: Dict[str, float] = {dst: origin_cost}
        successor: Dict[str, Tuple[str, _Record]] = {}
        settled: Set[str] = set()
        heap: List[Tuple[float, str]] = [(origin_cost, dst)]

        while heap:
            _, node_v = heappop(heap)
            if node_v in settled:
                continue
            settled.add(node_v)
            if node_v == src:
                break

            amount_at_v, weight_at_v, prob_at_v, cltv_at_v, hops_at_v, htlc_min_at_v = label[node_v]

            for record in index.incoming(node_v):
                node_u = record[_SRC]
                if node_u in settled or node_u in banned_nodes:
                    continue
                if banned_edges and (node_u, node_v, record[_SCID]) in banned_edges:
                    continue
                if not _edge_passes(record, amount_at_v, constraints):
                    continue

                # The sender originates rather than forwards: it charges neither a fee nor
                # a timelock delta on its own channel, so neither enters the cost.
                is_sender_hop = node_u == src
                if is_sender_hop:
                    fee_msat = 0
                    cltv_delta = 0
                else:
                    fee_msat = record[_BASE] + (amount_at_v * record[_PPM]) // 1_000_000
                    cltv_delta = record[_CLTV]

                context = EdgeContext(
                    amount_msat=amount_at_v,
                    fee_msat=fee_msat,
                    fee_base_msat=record[_BASE],
                    fee_ppm=record[_PPM],
                    cltv_expiry_delta=cltv_delta,
                    htlc_minimum_msat=record[_HMIN],
                    htlc_maximum_msat=None if record[_HMAX] == _UNBOUNDED else record[_HMAX],
                    capacity_msat=record[_CAP_EFF],
                    funding_block=record[_BLOCK],
                    is_sender_hop=is_sender_hop,
                )

                edge_prob = weight_fn.edge_probability(context)
                new_prob = prob_at_v * (edge_prob if edge_prob > _MIN_PROBABILITY else _MIN_PROBABILITY)
                new_cltv = cltv_at_v + cltv_delta
                new_hops = hops_at_v + 1
                amount_at_u = amount_at_v + fee_msat

                # Each accumulator is a suffix sum that only grows toward the source, so a
                # partial path that already breaches a bound can never recover: pruning
                # here is exact, not a heuristic.
                if max_cltv is not None and new_cltv > max_cltv:
                    continue
                if min_prob is not None and new_prob < min_prob:
                    continue
                if max_len is not None and new_hops > max_len:
                    continue
                if fee_limit is not None and amount_at_u - amount_msat > fee_limit:
                    continue

                new_weight = weight_at_v + weight_fn.edge_weight(context)
                new_htlc_min = weight_fn.path_htlc_minimum_msat(context, htlc_min_at_v)
                new_dist = weight_fn.path_cost(
                    new_weight, amount_at_u - amount_msat, new_htlc_min, new_prob, attempt_cost
                )

                if new_dist < dist.get(node_u, float("inf")):
                    dist[node_u] = new_dist
                    label[node_u] = (amount_at_u, new_weight, new_prob, new_cltv, new_hops, new_htlc_min)
                    successor[node_u] = (node_v, record)
                    heappush(heap, (new_dist, node_u))

        if src not in label:
            return None
        return self._materialise(label, successor, src, dst, amount_sat, dist.get(src, 0.0))

    def _materialise(
        self,
        label: Dict[str, _Label],
        successor: Dict[str, Tuple[str, _Record]],
        src: str,
        dst: str,
        amount_sat: int,
        total_cost: float,
    ) -> Optional[Route]:
        """Turn search state into a :class:`Route`, tagging it with the client's cost.

        ``total_weight`` records the value the client actually minimised, not the bare
        additive sum, so routes stay comparable to what the client's own queue saw.
        """
        amounts = {node: state[0] for node, state in label.items()}
        route = _build_route(amounts, successor, src, dst, amount_sat)
        if route is None:
            return None
        route.total_weight = total_cost
        route.success_probability = label[src][2]
        route.client = self.profile.label
        return route

    # ── constraints ──────────────────────────────────────────────────────────────

    def _rejected(self, route: Route, amount_sat: int, limits: PathConstraints) -> bool:
        """Does the finished path breach a side constraint?

        Always applied. For clients that already pruned during the search this is a
        no-op; for CLN and eclair, which validate afterwards, it is the only check — and
        it can reject a payment outright even when some compliant path existed, exactly as
        those clients do.
        """
        if limits.max_path_length is not None and route.num_hops > limits.max_path_length:
            return True
        if limits.max_cltv_expiry_delta is not None and route.total_cltv > limits.max_cltv_expiry_delta:
            return True
        if limits.min_path_probability is not None and route.success_probability < limits.min_path_probability:
            return True
        fee_limit = limits.fee_limit_msat(amount_sat * 1000)
        return fee_limit is not None and route.total_fee_msat > fee_limit

    # ── Yen's K-shortest paths ───────────────────────────────────────────────────

    def _yen(
        self,
        index: RoutingIndex,
        src: str,
        dst: str,
        amount_sat: int,
        constraints: Constraint,
        limits: PathConstraints,
        best: Optional[Route],
    ) -> List[Route]:
        """Yen's algorithm over the client's weight, as eclair runs it.

        Each spur search is only a *proposal*: it treats the spur node as the sender, so it
        under-prices that node's own hop and mis-sizes every amount upstream of it. The
        spliced path is therefore re-costed end to end by :meth:`_evaluate` before it can
        compete, which is also what makes the amount-dependence of fees come out right.
        """
        if best is None:
            return []

        confirmed: List[Route] = [best]
        seen = {self._path_key(best)}
        candidates: List[Tuple[float, Tuple[_HopSpec, ...], Route]] = []

        while len(confirmed) < self.profile.k_paths:
            previous = confirmed[-1]
            for index_of_spur in range(previous.num_hops):
                spur_node = previous.path[index_of_spur]
                root = self._path_key(previous)[:index_of_spur]

                # Ban the continuation every already-accepted path took from here, so the
                # spur is forced to diverge, and ban the root's interior nodes so it cannot
                # loop back into ground the root already covers.
                banned_edges = {
                    self._path_key(other)[index_of_spur]
                    for other in confirmed
                    if other.num_hops > index_of_spur and self._path_key(other)[:index_of_spur] == root
                }
                banned_nodes = frozenset(previous.path[:index_of_spur])

                spur = self._dijkstra(
                    index, spur_node, dst, amount_sat, constraints, limits, banned_nodes, frozenset(banned_edges)
                )
                if spur is None:
                    continue

                spliced = root + self._path_key(spur)
                if spliced in seen:
                    continue
                route = self._evaluate(index, spliced, amount_sat, constraints)
                if route is None:
                    continue
                seen.add(spliced)
                candidates.append((route.total_weight, spliced, route))

            if not candidates:
                break
            # Ties broken on the path itself so the K-set is deterministic across runs.
            candidates.sort(key=lambda item: (item[0], item[1]))
            confirmed.append(candidates.pop(0)[2])

        return [route for route in confirmed if not self._rejected(route, amount_sat, limits)]

    @staticmethod
    def _path_key(route: Route) -> Tuple[_HopSpec, ...]:
        """Identify a route by the channels it uses, so parallels stay distinguishable."""
        return tuple((hop.src, hop.dst, hop.scid) for hop in route.hops)

    def _evaluate(
        self,
        index: RoutingIndex,
        hops: Tuple[_HopSpec, ...],
        amount_sat: int,
        constraints: Constraint,
    ) -> Optional[Route]:
        """Cost an explicit hop sequence end to end, or reject it as infeasible.

        Walks backwards so each hop is priced at the amount that truly crosses it, which a
        spliced path cannot inherit from its parts.
        """
        if not hops:
            return None
        src = hops[0][0]
        dst = hops[-1][1]

        # Splicing a root onto a spur can revisit a node; such a path is not simple and no
        # client would send over it.
        visited = [spec[0] for spec in hops] + [dst]
        if len(set(visited)) != len(visited):
            return None

        weight_fn = self.profile.weight
        amount_msat = amount_sat * 1000
        attempt_cost = weight_fn.attempt_cost_msat(amount_msat)
        label: Dict[str, _Label] = {dst: (amount_msat, 0.0, 1.0, 0, 0, 0)}
        successor: Dict[str, Tuple[str, _Record]] = {}

        amount_at_v, weight, probability, cltv, hop_count, htlc_min = amount_msat, 0.0, 1.0, 0, 0, 0
        for node_u, node_v, scid in reversed(hops):
            record = next(
                (item for item in index.incoming(node_v) if item[_SRC] == node_u and item[_SCID] == scid),
                None,
            )
            if record is None or not _edge_passes(record, amount_at_v, constraints):
                return None

            is_sender_hop = node_u == src
            fee_msat = 0 if is_sender_hop else record[_BASE] + (amount_at_v * record[_PPM]) // 1_000_000
            cltv_delta = 0 if is_sender_hop else record[_CLTV]

            context = EdgeContext(
                amount_msat=amount_at_v,
                fee_msat=fee_msat,
                fee_base_msat=record[_BASE],
                fee_ppm=record[_PPM],
                cltv_expiry_delta=cltv_delta,
                htlc_minimum_msat=record[_HMIN],
                htlc_maximum_msat=None if record[_HMAX] == _UNBOUNDED else record[_HMAX],
                capacity_msat=record[_CAP_EFF],
                funding_block=record[_BLOCK],
                is_sender_hop=is_sender_hop,
            )

            edge_prob = weight_fn.edge_probability(context)
            probability *= edge_prob if edge_prob > _MIN_PROBABILITY else _MIN_PROBABILITY
            weight += weight_fn.edge_weight(context)
            htlc_min = weight_fn.path_htlc_minimum_msat(context, htlc_min)
            cltv += cltv_delta
            hop_count += 1
            amount_at_v += fee_msat

            successor[node_u] = (node_v, record)
            label[node_u] = (amount_at_v, weight, probability, cltv, hop_count, htlc_min)

        total_cost = weight_fn.path_cost(weight, amount_at_v - amount_msat, htlc_min, probability, attempt_cost)
        return self._materialise(label, successor, src, dst, amount_sat, total_cost)
