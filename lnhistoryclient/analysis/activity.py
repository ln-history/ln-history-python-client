"""Separating the nodes that carry payments from the ones that only send and receive them.

A Lightning snapshot is mostly ballast. Of the ~34 000 nodes in a 2026 snapshot, around
half hold no channel at all, and of those that do, the overwhelming majority can never
appear in the middle of a route — they have one channel, or no live policy, or their
second-largest channel is too small to pass the payment. Analysing "the network" without
saying which of those two populations you mean produces numbers that describe the
ballast.

This module makes the split explicit and computable from a single snapshot.

The headline result of the study behind this module is that the split needs **no fitted
threshold**. Two structural tests do the whole job:

``PASSIVE``
    There do not exist two distinct channels — one to receive on, one to send on — that
    both pass BOLT #7's hard constraints at this amount. The node cannot appear in the
    middle of a route under any client's pathfinding. 90.1% of a 2026 snapshot.

``MARGINAL``
    Could forward, but sits outside the strongly-connected routable core, so traffic
    cannot both reach it and leave it. A small class (0.5%) and empirically idle.

``ACTIVE``
    Both tests pass. On the anchor snapshot these 9.3% of nodes carried all of the
    forwarding in 13 745 simulated payments, and deleting everything else changed neither
    which payments succeeded nor what they cost.

So "active" here is not a ranking cut-off dressed up as a class. It is the set of nodes a
payment can physically traverse, and it happens to be an order of magnitude smaller than
the node count everyone quotes.

**Be precise about what that containment proves.** ``max_forward_sat`` is assembled from
directions that already pass the constraints at the amount, so it is either ``0`` or at
least the amount, never in between — "nothing below the gate ever forwards" is closer to a
theorem than to a measurement, and the simulation is a correctness check on the arithmetic
rather than a discovery. The findings worth quoting are the magnitudes: 90.7% of the node
set removed at no cost, and **56% of what survives never used at all**. Capability is not
use, and nothing in :class:`NodeProfile` predicts use well enough to filter on.

The quantity the line is drawn on is :attr:`NodeProfile.max_forward_sat`, the largest
single payment the node could forward. It is a min-cut on the node's own star: passing
amount ``a`` through node ``v`` needs one usable channel to receive on and a *different*
usable channel to send on, both admitting ``a``, so the ceiling is set by the
second-largest usable channel rather than by the total. That distinction is the whole
game for hub-and-spoke nodes: a node with one 10 BTC channel and nine 200 000 sat
channels has 12 BTC of "capacity" and can forward 200 000 sat.

Usability is decided by :func:`~lnhistoryclient.analysis.routing._edge_passes`, the same
predicate every router in this package uses, so "usable channel" cannot drift between
this module and the pathfinders that consume its output.

Two caveats that matter on archived data:

* **A missing ``channel_update`` is indistinguishable from a passive node.** The archive's
  policy coverage is partial (see the ``coverage`` figures in the study cache), and a
  direction with no policy is unusable by every real client — correctly so, since a client
  reading the same gossip would also refuse it. The classification is therefore a
  statement about *what the snapshot shows*, and it under-counts active nodes wherever the
  archive is thin. Screen dates by coverage before comparing across time.
* **:attr:`NodeProfile.policy_age_days` is partly an archive artifact.** Collector
  outages put holes in the update history at fixed calendar positions, so a stale-looking
  policy may mean the collector was down rather than the node was quiet. It is reported
  because it is useful cross-sectionally, and deliberately kept *out* of
  :data:`DEFAULT_RULE`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import networkx as nx

from lnhistoryclient.analysis.routing import (
    _ATTRS,
    _SCID,
    _SRC,
    Constraint,
    RoutingIndex,
    _edge_passes,
)
from lnhistoryclient.analysis.weights import DEFAULT_AMOUNT_SAT

logger = logging.getLogger(__name__)

#: Everything gossip can assert. ``BALANCE`` is excluded: it is not in gossip and would
#: fail closed on any graph without assigned balances.
STRUCTURAL: Constraint = Constraint.POLICY | Constraint.DISABLED | Constraint.HTLC_BOUNDS | Constraint.CAPACITY

_SECONDS_PER_DAY = 86_400.0


class ActivityClass(str, Enum):
    """Which of the three populations a node belongs to at a given payment amount."""

    #: Cannot appear as an intermediate hop at all — a structural fact about the snapshot.
    PASSIVE = "passive"
    #: Could forward, but sits outside the routable core — traffic cannot both reach it
    #: and leave it, so nothing routes through it however capable it looks.
    MARGINAL = "marginal"
    #: The routing substrate. Payments actually depend on these.
    ACTIVE = "active"

    def __str__(self) -> str:  # keeps DataFrame columns readable
        return self.value


@dataclass(frozen=True)
class NodeProfile:
    """Everything one snapshot can say about a node's ability to forward a payment.

    All ``*_sat`` fields are satoshis; all counts are channels (not directed edges), so a
    node with one channel used in both directions has ``channels == 1``.
    """

    node_id: str
    #: Distinct channels (parallel channels to the same peer count separately).
    channels: int
    #: Distinct counterparties.
    peers: int
    #: Sum of channel capacities. Deliberately *not* the basis of the line — see
    #: :attr:`max_forward_sat`.
    capacity_sat: int
    #: Channels whose inbound direction (peer → node) passes the constraints.
    live_in: int
    #: Channels whose outbound direction (node → peer) passes the constraints.
    live_out: int
    #: Channels usable in both directions.
    bidirectional: int
    #: Largest single payment this node could forward: the min-cut on its own star.
    max_forward_sat: int
    #: Node's own outbound directions carrying an explicit ``disabled`` flag.
    disabled_out: int
    #: Channels on which the node has published no ``channel_update`` at all. On archived
    #: data this conflates "node never priced it" with "archive missed it".
    unpriced_out: int
    #: Days since the node's freshest own policy. ``None`` when it published none.
    policy_age_days: Optional[float]
    #: Median outbound fee rate over the node's own live policies.
    median_fee_ppm: Optional[int]
    #: Whether a ``node_announcement`` was seen.
    announced: bool
    #: Member of :meth:`RoutingIndex.routable_core` at this amount.
    in_core: bool
    #: Assigned by :meth:`ActivityRule.classify`.
    activity: ActivityClass = ActivityClass.PASSIVE

    @property
    def is_active(self) -> bool:
        """The binary reading: does this node carry the network's payments?"""
        return self.activity is ActivityClass.ACTIVE

    @property
    def can_forward(self) -> bool:
        """Whether *any* route could pass through it — ``ACTIVE`` or ``MARGINAL``."""
        return self.activity is not ActivityClass.PASSIVE

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["activity"] = self.activity.value
        return payload


@dataclass(frozen=True)
class ActivityRule:
    """Where the line between ``MARGINAL`` and ``ACTIVE`` sits, and on what.

    The default rule has **no fitted threshold in it at all**, which is the study's main
    result rather than an omission. Two structural tests turn out to be jointly exact:

    ``max_forward_sat >= amount_sat``
        There exist two distinct channels, one usable inbound and one usable outbound,
        both admitting this amount.
    ``in_core``
        The node is in the strongly-connected routable core, so traffic can reach it and
        leave it.

    On the anchor snapshot (2026-07-01, 100 000 sat, 13 745 routed payments) the 3 231
    nodes satisfying both carried all 30 520 forwarding events. Every other node —
    including the 180 that pass the gate but sit outside the core, and the 1 169 in the
    core that cannot forward — carried exactly zero. Restricting transit to that set is
    *lossless*: every payment still routes, at identical cost.

    Both containments are largely structural rather than surprising (see the module
    docstring). What the numbers earn is the **sizing**: dropping either test would readmit
    over a thousand nodes that carry nothing.

    So the active/passive line is not a tuning choice; it is a fact about the graph.
    :attr:`min_live_channels` exists only to tighten the set further when a caller wants a
    smaller one, and every positive value is **lossy** — see the cost table in the study
    notebook (``>= 4`` keeps 1 677 nodes and 93.6% of routing load, ``>= 10`` keeps 837
    and 85.9%).
    """

    #: Payment size the profile was computed at. Everything here is amount-relative.
    amount_sat: int = DEFAULT_AMOUNT_SAT
    #: Optional tightening: minimum channels usable in *both* directions. ``0`` disables
    #: it, which is the default because every positive value discards nodes that do carry
    #: traffic — see ``analyse_activity.tightening_cost`` for what each one costs.
    min_live_channels: int = 0
    #: Require membership of the strongly-connected routable core. Load-bearing: 180 nodes
    #: on the anchor snapshot pass the forwarding gate from outside the core and forward
    #: nothing.
    require_core: bool = True

    def at_amount(self, amount_sat: int) -> "ActivityRule":
        """The same rule rebased to a different payment size."""
        return ActivityRule(
            amount_sat=amount_sat,
            min_live_channels=self.min_live_channels,
            require_core=self.require_core,
        )

    def classify(self, profile: NodeProfile) -> ActivityClass:
        """Place one node in one of the three populations."""
        if profile.max_forward_sat < self.amount_sat:
            return ActivityClass.PASSIVE
        if self.require_core and not profile.in_core:
            return ActivityClass.MARGINAL
        if profile.bidirectional < self.min_live_channels:
            return ActivityClass.MARGINAL
        return ActivityClass.ACTIVE

    def describe(self) -> str:
        parts = [f"can forward {self.amount_sat:,} sat"]
        if self.require_core:
            parts.append("in the routable core")
        if self.min_live_channels:
            parts.append(f">= {self.min_live_channels} two-way channels")
        return ", ".join(parts)


#: The line. Both clauses are structural rather than fitted, so there is nothing here to
#: tune. Every positive ``min_live_channels`` trades recall for a smaller set — see
#: :func:`node_profiles` and :func:`strong_core`.
DEFAULT_RULE = ActivityRule()


def _direction_ceiling_sat(attrs: Dict[str, Any], floor_sat: int) -> int:
    """Largest HTLC this direction can carry, from whichever bounds the snapshot knows.

    ``floor_sat`` is returned when neither bound is present. The caller only ever asks
    about directions that already passed the constraints at ``floor_sat``, so that is a
    true lower bound rather than a guess.
    """
    bounds: List[int] = []
    capacity = attrs.get("capacity_sat")
    if isinstance(capacity, int) and capacity > 0:
        bounds.append(capacity)
    htlc_max = attrs.get("htlc_maximum_msat")
    if isinstance(htlc_max, int) and htlc_max > 0:
        bounds.append(htlc_max // 1000)
    return min(bounds) if bounds else floor_sat


def _max_forward_sat(inbound: Dict[int, int], outbound: Dict[int, int]) -> int:
    """Largest amount that can enter on one channel and leave on a *different* one.

    ``max over c_in != c_out of min(inbound[c_in], outbound[c_out])``. Sorting both sides
    once makes this three comparisons rather than a quadratic scan: the best pair is the
    two maxima unless they are the same channel, in which case it is the better of the two
    ways to break the tie.
    """
    if not inbound or not outbound:
        return 0
    top_in = sorted(inbound.items(), key=lambda item: item[1], reverse=True)[:2]
    top_out = sorted(outbound.items(), key=lambda item: item[1], reverse=True)[:2]
    best = 0
    for scid_in, ceiling_in in top_in:
        for scid_out, ceiling_out in top_out:
            if scid_in != scid_out:
                best = max(best, min(ceiling_in, ceiling_out))
    return best


def node_profiles(
    graph: nx.MultiDiGraph,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
    *,
    rule: Optional[ActivityRule] = None,
    constraints: Constraint = STRUCTURAL,
    as_of: Optional[int] = None,
    index: Optional[RoutingIndex] = None,
) -> Dict[str, NodeProfile]:
    """Profile and classify every node in a snapshot at one payment size.

    Args:
        graph: Canonical ``MultiDiGraph`` from
            :func:`~lnhistoryclient.graph.build_multidigraph`. Attach real capacities
            first — the ``htlc_maximum_msat`` fallback understates forwarding ceilings on
            channels whose owner advertises a conservative maximum.
        amount_sat: The payment size everything is judged against. Forwarding ability is
            not a property of a node alone; a node that routes 10 000 sat all day may be
            unable to pass 5 000 000.
        rule: Where to put the ``ACTIVE`` line. Defaults to :data:`DEFAULT_RULE` rebased
            to ``amount_sat``.
        constraints: Hard constraints defining a usable direction. Defaults to everything
            gossip can assert.
        as_of: Unix time the snapshot represents, for ``policy_age_days``. Defaults to the
            newest ``channel_update`` in the graph, which is what the snapshot itself
            implies.
        index: Prebuilt :class:`RoutingIndex`, if the caller already has one.

    Returns:
        ``node_id`` → :class:`NodeProfile`, one entry per node in ``graph``.
    """
    active_rule = (rule or DEFAULT_RULE).at_amount(amount_sat)
    routing_index = index if index is not None else RoutingIndex(graph)
    amount_msat = amount_sat * 1000

    inbound: Dict[str, Dict[int, int]] = {}
    outbound: Dict[str, Dict[int, int]] = {}
    channels: Dict[str, Set[int]] = {}
    peers: Dict[str, Set[str]] = {}
    capacity: Dict[str, int] = {}
    disabled_out: Dict[str, int] = {}
    priced_out: Dict[str, Set[int]] = {}
    fee_rates: Dict[str, List[int]] = {}
    newest_policy: Dict[str, int] = {}
    latest_seen = 0

    for source, target, key, attrs in graph.edges(keys=True, data=True):
        scid = key if isinstance(key, int) else int(attrs.get("scid") or 0)
        channels.setdefault(source, set()).add(scid)
        channels.setdefault(target, set()).add(scid)
        peers.setdefault(source, set()).add(target)
        peers.setdefault(target, set()).add(source)

        # Capacity is a property of the channel, so count it once per endpoint: the
        # reverse direction of the same scid contributes the same number to the peer.
        raw_capacity = attrs.get("capacity_sat")
        if isinstance(raw_capacity, int):
            capacity[source] = capacity.get(source, 0) + raw_capacity

        if attrs.get("has_update"):
            priced_out.setdefault(source, set()).add(scid)
            timestamp = attrs.get("timestamp")
            if isinstance(timestamp, int):
                latest_seen = max(latest_seen, timestamp)
                if timestamp > newest_policy.get(source, 0):
                    newest_policy[source] = timestamp
            if attrs.get("disabled"):
                disabled_out[source] = disabled_out.get(source, 0) + 1
            else:
                rate = attrs.get("fee_proportional_millionths")
                if isinstance(rate, int):
                    fee_rates.setdefault(source, []).append(rate)

    # Usability is decided by the routers' own predicate, over the index's records, so
    # this module and the pathfinders can never disagree about what a usable channel is.
    for target, records in routing_index.predecessors.items():
        for record in records:
            if not _edge_passes(record, amount_msat, constraints):
                continue
            scid = record[_SCID]
            ceiling = _direction_ceiling_sat(record[_ATTRS], amount_sat)
            # One record per (target, scid) on each side, so these are plain assignments;
            # max() only guards a graph carrying duplicate directed edges for one channel.
            into = inbound.setdefault(target, {})
            into[scid] = max(into.get(scid, 0), ceiling)
            out_of = outbound.setdefault(record[_SRC], {})
            out_of[scid] = max(out_of.get(scid, 0), ceiling)

    reference = as_of if as_of is not None else latest_seen
    # Computed unconditionally, even when the rule ignores it: ``in_core`` is a reported
    # observation, and leaving it False because nothing asked would be a silent lie.
    core = routing_index.routable_core(amount_sat, constraints)

    profiles: Dict[str, NodeProfile] = {}
    for node_id, node_attrs in graph.nodes(data=True):
        node_in = inbound.get(node_id, {})
        node_out = outbound.get(node_id, {})
        node_channels = channels.get(node_id, set())
        rates = sorted(fee_rates.get(node_id, []))
        newest = newest_policy.get(node_id)

        profile = NodeProfile(
            node_id=node_id,
            channels=len(node_channels),
            peers=len(peers.get(node_id, set())),
            capacity_sat=capacity.get(node_id, 0),
            live_in=len(node_in),
            live_out=len(node_out),
            bidirectional=len(set(node_in) & set(node_out)),
            max_forward_sat=_max_forward_sat(node_in, node_out),
            disabled_out=disabled_out.get(node_id, 0),
            unpriced_out=len(node_channels - priced_out.get(node_id, set())),
            policy_age_days=None if newest is None else max(0.0, (reference - newest) / _SECONDS_PER_DAY),
            median_fee_ppm=rates[len(rates) // 2] if rates else None,
            announced=bool(node_attrs.get("announced")),
            in_core=node_id in core,
        )
        profiles[node_id] = replace_activity(profile, active_rule.classify(profile))

    counts = summarise(profiles)
    logger.info(
        "node_profiles @ %d sat: %d active, %d marginal, %d passive (of %d)",
        amount_sat,
        counts[ActivityClass.ACTIVE],
        counts[ActivityClass.MARGINAL],
        counts[ActivityClass.PASSIVE],
        len(profiles),
    )
    return profiles


def replace_activity(profile: NodeProfile, activity: ActivityClass) -> NodeProfile:
    """``NodeProfile`` is frozen; this is the one sanctioned way to set its class."""
    return NodeProfile(**{**asdict(profile), "activity": activity})


def summarise(profiles: Dict[str, NodeProfile]) -> Dict[ActivityClass, int]:
    """How many nodes fall in each population."""
    counts = {member: 0 for member in ActivityClass}
    for profile in profiles.values():
        counts[profile.activity] += 1
    return counts


def active_nodes(profiles: Dict[str, NodeProfile]) -> Set[str]:
    """The ``ACTIVE`` node ids."""
    return {node_id for node_id, profile in profiles.items() if profile.is_active}


def strong_core(
    graph: nx.MultiDiGraph,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
    *,
    rule: Optional[ActivityRule] = None,
    constraints: Constraint = STRUCTURAL,
    connected: bool = True,
    profiles: Optional[Dict[str, NodeProfile]] = None,
) -> nx.MultiDiGraph:
    """Filter a snapshot down to the nodes that actually carry payments.

    The result is the subgraph induced on the ``ACTIVE`` nodes — a routing substrate you
    can hand to any analysis in this package without the surrounding ballast dominating
    every average.

    Args:
        graph: Canonical ``MultiDiGraph``, capacity-enriched.
        amount_sat: Payment size the core is defined for. The core *shrinks* as this
            grows; there is no single amount-free core, and reusing one across a sweep
            silently mixes populations.
        rule: Where the line sits. Defaults to :data:`DEFAULT_RULE`.
        constraints: What counts as a usable direction.
        connected: Keep only the largest strongly-connected component of the filtered
            graph, so every member can pay every other. Turning this off leaves stubs that
            satisfy the node-level test but hang off the core by a single direction.
        profiles: Precomputed profiles, to avoid a second pass.

    Returns:
        A node-induced subgraph *copy* — safe to mutate, and carrying every original edge
        between surviving nodes, including directions that failed the constraints. Filter
        edges too if you need a strictly usable graph.
    """
    resolved = (
        profiles if profiles is not None else node_profiles(graph, amount_sat, rule=rule, constraints=constraints)
    )
    keep = active_nodes(resolved)
    if connected and keep:
        keep = _largest_strong_component(graph, keep, amount_sat, constraints)
    core = graph.subgraph(keep).copy()
    logger.info(
        "strong_core @ %d sat: %d of %d nodes (%.1f%%), %d of %d channels",
        amount_sat,
        core.number_of_nodes(),
        graph.number_of_nodes(),
        100.0 * core.number_of_nodes() / max(graph.number_of_nodes(), 1),
        core.number_of_edges() // 2,
        graph.number_of_edges() // 2,
    )
    return core


def _largest_strong_component(
    graph: nx.MultiDiGraph,
    keep: Set[str],
    amount_sat: int,
    constraints: Constraint,
) -> Set[str]:
    """Largest set within ``keep`` whose members can all pay each other at ``amount_sat``."""
    amount_msat = amount_sat * 1000
    reachable = nx.DiGraph()
    reachable.add_nodes_from(keep)
    index = RoutingIndex(graph.subgraph(keep))
    for target, records in index.predecessors.items():
        for record in records:
            if _edge_passes(record, amount_msat, constraints):
                reachable.add_edge(record[_SRC], target)
    components = list(nx.strongly_connected_components(reachable))
    if not components:
        return set()
    # Ties broken on the smallest member so the core is reproducible across processes.
    largest = max(components, key=lambda part: (len(part), min(part)))
    return set(largest) if len(largest) > 1 else set()


def profile_frame(profiles: Dict[str, NodeProfile]) -> Any:
    """Tidy ``pandas`` frame, one row per node. Requires the ``analysis`` extra."""
    import pandas as pd

    return pd.DataFrame([profile.to_dict() for profile in profiles.values()])


def activity_curve(
    profiles: Dict[str, NodeProfile],
    amounts_sat: Sequence[int],
) -> List[Tuple[int, int]]:
    """How many profiled nodes could forward each amount.

    Reads :attr:`NodeProfile.max_forward_sat` directly, so it is a *ceiling* curve at the
    amount the profiles were computed at — cheap, and exact for amounts at or below it.
    Recompute the profiles per amount when you need the curve above it, because the
    usable-direction set itself changes with the amount.
    """
    ceilings = sorted(profile.max_forward_sat for profile in profiles.values())
    out: List[Tuple[int, int]] = []
    for amount in amounts_sat:
        # Number of ceilings >= amount, by binary search on the sorted list.
        low, high = 0, len(ceilings)
        while low < high:
            mid = (low + high) // 2
            if ceilings[mid] < amount:
                low = mid + 1
            else:
                high = mid
        out.append((int(amount), len(ceilings) - low))
    return out
