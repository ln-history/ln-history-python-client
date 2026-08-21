"""Payment simulation between nodes of a snapshot graph.

:func:`simulate_payment` routes a single payment; :func:`simulate_random_payments` runs a
seeded Monte-Carlo over random node pairs and aggregates success rate, fee and hop
distributions.

Liquidity
---------
When the graph has been populated by
:func:`~lnhistoryclient.graph.balances.assign_balances`, routes must satisfy the assigned
balances and a payment can optionally *consume* them. Without balances the simulation is
balance-agnostic exactly as before, and a "success" only means a route satisfying the
policy/htlc/capacity constraints exists — an upper bound on routability.

Remember that balances are **invented** (BOLT #7 never reveals them), so a success rate
computed against them is conditional on the scenario, not a measurement of the real
network. ``graph.graph["balance_scenario"]`` records which scenario produced it.

Commit semantics
----------------
By default a payment does not move liquidity: every trial probes the same pristine state,
so results are order-independent and the routing index is built once. ``commit=True``
(single) or ``sequential=True`` (Monte-Carlo) shifts balances along each successful path,
making the run path-dependent — which is the point when studying depletion, since the
decay of the success rate *is* the result.

Failure attribution
-------------------
A failed payment is re-routed with constraints dropped one tier at a time, and the first
tier that succeeds names the cause. This runs only on failures and short-circuits, and it
is what separates "the pair is not connected at all" from "connected but the liquidity is
on the wrong side" — the distinction that motivates modelling balances in the first place.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple, Union

import networkx as nx
import numpy as np

from lnhistoryclient.analysis.routing import (
    Constraint,
    ReverseDijkstraRouter,
    Route,
    RoutingIndex,
    RoutingStrategy,
)
from lnhistoryclient.analysis.weights import DEFAULT_AMOUNT_SAT
from lnhistoryclient.graph.balances import BALANCES_KEY, SCENARIO_KEY, apply_balance_delta
from lnhistoryclient.graph.projections import edge_capacity_sat

logger = logging.getLogger(__name__)

#: Constraint tiers dropped in order on failure; the first that routes names the cause.
#: Ordered most-specific to least, so the reported reason is the tightest explanation.
_DIAGNOSIS_LADDER: Tuple[Tuple[Constraint, str], ...] = (
    (Constraint.ALL & ~Constraint.BALANCE, "insufficient_liquidity"),
    (Constraint.ALL & ~(Constraint.BALANCE | Constraint.CAPACITY), "insufficient_capacity"),
    (Constraint.POLICY | Constraint.DISABLED, "htlc_bounds"),
    (Constraint.POLICY, "disabled_channels"),
    (Constraint.NONE, "no_channel_update"),
)


# ── payment amounts ──────────────────────────────────────────────────────────────


class AmountDistribution(ABC):
    """A distribution over payment amounts in satoshis."""

    @abstractmethod
    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Draw ``n`` amounts in satoshis."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class FixedAmount(AmountDistribution):
    """Every payment is the same size — the default, and the clearest comparison."""

    def __init__(self, amount_sat: int = DEFAULT_AMOUNT_SAT) -> None:
        if amount_sat <= 0:
            raise ValueError(f"amount_sat must be positive, got {amount_sat}")
        self.amount_sat = int(amount_sat)

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return np.full(n, self.amount_sat, dtype=np.int64)

    def __repr__(self) -> str:
        return f"FixedAmount({self.amount_sat})"


class LogNormalAmount(AmountDistribution):
    """Log-normal amounts — real payment sizes span orders of magnitude.

    ``median_sat`` is the median (not the mean); ``sigma`` is the standard deviation of
    the underlying normal, so larger values widen the spread multiplicatively.
    """

    def __init__(self, median_sat: int = 10_000, sigma: float = 2.0, min_sat: int = 1) -> None:
        if median_sat <= 0 or sigma < 0:
            raise ValueError(f"median_sat must be positive and sigma non-negative, got {median_sat}, {sigma}")
        self.median_sat = int(median_sat)
        self.sigma = float(sigma)
        self.min_sat = int(min_sat)

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        drawn = rng.lognormal(mean=float(np.log(self.median_sat)), sigma=self.sigma, size=n)
        return np.maximum(drawn.astype(np.int64), self.min_sat)

    def __repr__(self) -> str:
        return f"LogNormalAmount(median_sat={self.median_sat}, sigma={self.sigma})"


class UniformAmount(AmountDistribution):
    """Amounts drawn uniformly from ``[lo_sat, hi_sat]``."""

    def __init__(self, lo_sat: int = 1_000, hi_sat: int = 1_000_000) -> None:
        if not 0 < lo_sat <= hi_sat:
            raise ValueError(f"require 0 < lo_sat <= hi_sat, got {lo_sat}, {hi_sat}")
        self.lo_sat = int(lo_sat)
        self.hi_sat = int(hi_sat)

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.integers(self.lo_sat, self.hi_sat + 1, size=n, dtype=np.int64)

    def __repr__(self) -> str:
        return f"UniformAmount({self.lo_sat}, {self.hi_sat})"


class NodeSelection(str, Enum):
    """How source/destination pairs are drawn.

    ``UNIFORM`` treats every node equally, which over-represents the leaf-to-leaf pairs
    that dominate the node count but carry little real traffic. The weighted options bias
    selection toward well-connected or high-liquidity nodes, closer to observed usage.
    """

    UNIFORM = "uniform"
    BY_DEGREE = "by_degree"
    BY_CAPACITY = "by_capacity"


# ── results ──────────────────────────────────────────────────────────────────────


@dataclass
class PaymentResult:
    """Outcome of a single simulated payment."""

    src: str
    dst: str
    amount_sat: int
    success: bool
    route: Optional[Route] = None
    failure_reason: Optional[str] = None
    committed: bool = False


@dataclass
class RandomPaymentSummary:
    """Aggregate of a Monte-Carlo run of random payments."""

    trials: int
    amount_sat: int
    num_success: int
    num_failure: int
    success_rate: float
    fees_msat: List[int] = field(default_factory=list)  # successful trials only
    hops: List[int] = field(default_factory=list)  # successful trials only
    amounts_sat: List[int] = field(default_factory=list)  # every trial
    failure_reasons: Dict[str, int] = field(default_factory=dict)
    results: List[PaymentResult] = field(default_factory=list)
    sequential: bool = False
    #: Channels with no assigned balance; they are unroutable under ``Constraint.BALANCE``.
    channels_without_balance: Optional[int] = None
    #: ``repr`` of the balance scenario in force, when the graph carries one.
    balance_scenario: Optional[str] = None
    #: Nodes eligible for selection, i.e. those with at least one channel.
    eligible_nodes: int = 0


# ── internals ────────────────────────────────────────────────────────────────────


def _require_multidigraph(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Routing needs parallel channels kept distinct; a collapsed projection cannot."""
    if not isinstance(graph, nx.MultiDiGraph):
        raise TypeError(
            "balance-aware routing requires the canonical MultiDiGraph from build_multidigraph(); "
            f"got {type(graph).__name__}. A simple projection merges parallel channels, which "
            "would let one payment draw on liquidity spread across several of them."
        )
    return graph


def _constraints_for(graph: nx.MultiDiGraph) -> Constraint:
    """Enforce balances only when the graph actually has them."""
    if graph.graph.get(BALANCES_KEY):
        return Constraint.ALL
    return Constraint.ALL & ~Constraint.BALANCE


def _commit_route(graph: nx.MultiDiGraph, route: Route) -> None:
    """Shift liquidity along a successful route."""
    for hop in route.hops:
        # Round the debit up: hop amounts are msat, balances are sat.
        apply_balance_delta(graph, hop.src, hop.dst, hop.scid, -(-hop.amount_forwarded_msat // 1000))


def _diagnose(
    index: RoutingIndex,
    src: str,
    dst: str,
    amount_sat: int,
    strategy: RoutingStrategy,
    constraints: Constraint,
) -> str:
    """Name the tightest constraint whose removal makes the route feasible.

    For a client router the failure is decomposed first: a client's own fee / CLTV /
    probability budget is reported separately from anything the network did, so that the
    ladder's verdicts stay comparable *between* clients. Folding the two together would
    make LND look like it faced a different network — its cold-start minimum path
    probability alone rules out routes beyond about nine hops, which on a policy-sparse
    snapshot is most of them.
    """
    unconstrained = strategy.without_side_constraints()
    if unconstrained is not None:
        if unconstrained.find_route(index, src, dst, amount_sat, constraints) is not None:
            # The network offered a path and the client declined it. That is the whole
            # explanation, and calling it "unreachable" would be plainly untrue.
            return "client_side_constraints"
        # The client's limits are not the sole cause, so attribute what remains to the
        # network alone rather than letting them mask the real tier.
        strategy = unconstrained

    for relaxed, reason in _DIAGNOSIS_LADDER:
        # Never *add* a constraint the caller had already disabled.
        candidate = relaxed & constraints
        if candidate == constraints:
            continue
        if strategy.find_route(index, src, dst, amount_sat, candidate) is not None:
            return reason
    return "unreachable"


def _route_one(
    graph: nx.MultiDiGraph,
    index: RoutingIndex,
    src: str,
    dst: str,
    amount_sat: int,
    strategy: RoutingStrategy,
    constraints: Constraint,
    commit: bool,
    diagnose: bool,
) -> PaymentResult:
    """Run a single routing attempt against an already-built index."""
    if src == dst:
        return PaymentResult(src, dst, amount_sat, False, failure_reason="same_node")
    if src not in index or dst not in index:
        return PaymentResult(src, dst, amount_sat, False, failure_reason="unknown_node")

    route = strategy.find_route(index, src, dst, amount_sat, constraints)
    if route is None:
        reason = _diagnose(index, src, dst, amount_sat, strategy, constraints) if diagnose else "no_route"
        return PaymentResult(src, dst, amount_sat, False, failure_reason=reason)

    if commit:
        _commit_route(graph, route)
    return PaymentResult(src, dst, amount_sat, True, route=route, committed=commit)


def _selection_weights(graph: nx.MultiDiGraph, nodes: Sequence[str], selection: NodeSelection) -> Optional[np.ndarray]:
    """Probability weights over ``nodes``, or ``None`` for uniform selection."""
    if selection is NodeSelection.UNIFORM:
        return None

    if selection is NodeSelection.BY_DEGREE:
        raw = np.array([graph.degree(node) for node in nodes], dtype=float)
    else:  # BY_CAPACITY
        totals: Dict[str, float] = {node: 0.0 for node in nodes}
        for src, _dst, attrs in graph.edges(data=True):
            capacity = edge_capacity_sat(attrs)
            if capacity and src in totals:
                totals[src] += capacity
        raw = np.array([totals[node] for node in nodes], dtype=float)

    total = float(raw.sum())
    if total <= 0:
        logger.warning("%s selection has zero total weight; falling back to uniform", selection.value)
        return None
    return np.asarray(raw / total, dtype=float)


# ── public API ───────────────────────────────────────────────────────────────────


def simulate_payment(
    graph: nx.MultiDiGraph,
    src: str,
    dst: str,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
    strategy: Optional[RoutingStrategy] = None,
    *,
    commit: bool = False,
    diagnose: bool = True,
    index: Optional[RoutingIndex] = None,
    constraints: Optional[Constraint] = None,
) -> PaymentResult:
    """Simulate one payment of ``amount_sat`` from ``src`` to ``dst``.

    Args:
        graph: Canonical ``MultiDiGraph`` from ``build_multidigraph``.
        src: Sender node id (hex).
        dst: Recipient node id (hex).
        amount_sat: Payment amount in satoshis.
        strategy: Routing strategy (defaults to :class:`ReverseDijkstraRouter`).
        commit: Shift balances along the path on success.
        diagnose: On failure, attribute the cause via the relaxation ladder.
        index: Reuse a prebuilt :class:`RoutingIndex` instead of building one.
        constraints: Override the enforced constraints. Defaults to everything, minus
            ``BALANCE`` when the graph has no balances assigned.

    Returns:
        A :class:`PaymentResult`. ``failure_reason`` is ``"same_node"``,
        ``"unknown_node"``, or a ladder verdict such as ``"insufficient_liquidity"`` /
        ``"unreachable"`` (or plain ``"no_route"`` when ``diagnose=False``).
    """
    _require_multidigraph(graph)
    strategy = strategy or ReverseDijkstraRouter()
    index = index or RoutingIndex(graph)
    constraints = _constraints_for(graph) if constraints is None else constraints
    return _route_one(graph, index, src, dst, amount_sat, strategy, constraints, commit, diagnose)


def simulate_random_payments(
    graph: nx.MultiDiGraph,
    n: int = 1000,
    amount_sat: Union[int, AmountDistribution] = DEFAULT_AMOUNT_SAT,
    seed: Optional[int] = None,
    strategy: Optional[RoutingStrategy] = None,
    *,
    sequential: bool = False,
    diagnose: bool = True,
    selection: NodeSelection = NodeSelection.UNIFORM,
    constraints: Optional[Constraint] = None,
) -> RandomPaymentSummary:
    """Monte-Carlo simulation of ``n`` payments between randomly chosen node pairs.

    The routing index is built once and reused. Because balances are read live from the
    graph, that stays valid in ``sequential`` mode too — no rebuild per trial.

    Args:
        graph: Canonical ``MultiDiGraph``.
        n: Number of trials.
        amount_sat: A fixed amount, or an :class:`AmountDistribution` to draw per trial.
        seed: RNG seed for reproducible pair and amount selection.
        strategy: Routing strategy (defaults to :class:`ReverseDijkstraRouter`).
        sequential: Commit every successful payment, so later trials see a network
            drained by earlier ones. Makes the run order-dependent by design.
        diagnose: Attribute failures via the relaxation ladder.
        selection: How source/destination pairs are drawn.
        constraints: Override the enforced constraints.

    Returns:
        A :class:`RandomPaymentSummary`.
    """
    _require_multidigraph(graph)
    strategy = strategy or ReverseDijkstraRouter()
    index = RoutingIndex(graph)
    constraints = _constraints_for(graph) if constraints is None else constraints

    amounts_dist = amount_sat if isinstance(amount_sat, AmountDistribution) else FixedAmount(int(amount_sat))
    rng = np.random.default_rng(seed)

    # Nodes with no channels cannot send or receive. They are in the graph because
    # build_multidigraph keeps every announced node, and on a 2021 snapshot they are 38%
    # of the node set — including them would spend most trials manufacturing guaranteed
    # "unreachable" failures and would dilute every rate reported here.
    nodes = sorted(node for node in index.nodes if graph.degree(node) > 0)
    scenario = graph.graph.get(SCENARIO_KEY)
    unassigned = None
    if scenario is not None:
        unassigned = getattr(scenario, "n_channels", 0) - getattr(scenario, "n_assigned", 0)

    if len(nodes) < 2 or n <= 0:
        return RandomPaymentSummary(
            trials=0,
            amount_sat=getattr(amounts_dist, "amount_sat", 0),
            num_success=0,
            num_failure=0,
            success_rate=0.0,
            sequential=sequential,
            channels_without_balance=unassigned,
            balance_scenario=repr(scenario) if scenario is not None else None,
            eligible_nodes=len(nodes),
        )

    amounts = amounts_dist.sample(n, rng)
    weights = _selection_weights(graph, nodes, selection)

    results: List[PaymentResult] = []
    fees: List[int] = []
    hop_counts: List[int] = []
    failure_reasons: Dict[str, int] = {}
    num_success = 0

    for trial in range(n):
        picked = rng.choice(len(nodes), size=2, replace=False, p=weights)
        src, dst = nodes[int(picked[0])], nodes[int(picked[1])]
        amount = int(amounts[trial])

        result = _route_one(graph, index, src, dst, amount, strategy, constraints, commit=sequential, diagnose=diagnose)
        results.append(result)
        if result.success and result.route is not None:
            num_success += 1
            fees.append(result.route.total_fee_msat)
            hop_counts.append(result.route.num_hops)
        else:
            reason = result.failure_reason or "unknown"
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    return RandomPaymentSummary(
        trials=n,
        amount_sat=getattr(amounts_dist, "amount_sat", int(np.median(amounts))),
        num_success=num_success,
        num_failure=n - num_success,
        success_rate=num_success / n,
        fees_msat=fees,
        hops=hop_counts,
        amounts_sat=[int(a) for a in amounts],
        failure_reasons=failure_reasons,
        results=results,
        sequential=sequential,
        channels_without_balance=unassigned,
        balance_scenario=repr(scenario) if scenario is not None else None,
        eligible_nodes=len(nodes),
    )
