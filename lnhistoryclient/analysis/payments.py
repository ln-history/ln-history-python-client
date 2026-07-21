"""Payment simulation between nodes of a snapshot graph.

:func:`simulate_payment` routes a single payment; :func:`simulate_random_payments`
runs a seeded Monte-Carlo over random node pairs and aggregates a success rate and
fee/hop distributions.

Everything here is **balance-agnostic** (gossip never reveals channel balances), so a
"successful" payment means a route satisfying the fee/htlc/capacity/disabled
constraints exists — an upper bound on real-world routability. The routing algorithm
is pluggable via :class:`lnhistoryclient.analysis.routing.RoutingStrategy`; MPP can be
added there without touching this module's API.
"""

import logging
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

import networkx as nx

from lnhistoryclient.analysis.routing import (
    CheapestFeeRouter,
    Route,
    RoutingStrategy,
    routable_view,
)
from lnhistoryclient.analysis.weights import DEFAULT_AMOUNT_SAT
from lnhistoryclient.graph.projections import to_directed_simple

logger = logging.getLogger(__name__)


@dataclass
class PaymentResult:
    """Outcome of a single simulated payment."""

    src: str
    dst: str
    amount_sat: int
    success: bool
    route: Optional[Route] = None
    failure_reason: Optional[str] = None


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
    failure_reasons: Dict[str, int] = field(default_factory=dict)
    results: List[PaymentResult] = field(default_factory=list)


def _as_directed_simple(graph: Union[nx.MultiDiGraph, nx.DiGraph]) -> nx.DiGraph:
    """Accept the canonical MultiDiGraph or an already-projected DiGraph."""
    if isinstance(graph, nx.MultiDiGraph):
        return to_directed_simple(graph)
    return graph


def simulate_payment(
    graph: Union[nx.MultiDiGraph, nx.DiGraph],
    src: str,
    dst: str,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
    strategy: Optional[RoutingStrategy] = None,
) -> PaymentResult:
    """Simulate one payment of ``amount_sat`` from ``src`` to ``dst``.

    Args:
        graph: Canonical ``MultiDiGraph`` (projected internally) or a directed simple graph.
        src: Sender node id (hex).
        dst: Recipient node id (hex).
        amount_sat: Payment amount in satoshis.
        strategy: Routing strategy (defaults to :class:`CheapestFeeRouter`).

    Returns:
        A :class:`PaymentResult`. ``failure_reason`` is ``"same_node"``,
        ``"unknown_node"``, or ``"no_route"`` on failure.
    """
    strategy = strategy or CheapestFeeRouter()
    directed = _as_directed_simple(graph)
    routable = routable_view(directed, amount_sat)
    return _route_one(routable, src, dst, amount_sat, strategy)


def _route_one(
    routable: nx.DiGraph,
    src: str,
    dst: str,
    amount_sat: int,
    strategy: RoutingStrategy,
) -> PaymentResult:
    """Run a single routing attempt on an already-built routable view."""
    if src == dst:
        return PaymentResult(src, dst, amount_sat, False, failure_reason="same_node")
    if src not in routable or dst not in routable:
        return PaymentResult(src, dst, amount_sat, False, failure_reason="unknown_node")
    route = strategy.find_route(routable, src, dst, amount_sat)
    if route is None:
        return PaymentResult(src, dst, amount_sat, False, failure_reason="no_route")
    return PaymentResult(src, dst, amount_sat, True, route=route)


def simulate_random_payments(
    graph: Union[nx.MultiDiGraph, nx.DiGraph],
    n: int = 1000,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
    seed: Optional[int] = None,
    strategy: Optional[RoutingStrategy] = None,
) -> RandomPaymentSummary:
    """Monte-Carlo simulation of ``n`` payments between uniformly-random node pairs.

    The directed projection and routable view (which depend only on ``amount_sat``) are
    built once and reused across all trials. Node selection is seeded for
    reproducibility.

    Args:
        graph: Canonical ``MultiDiGraph`` or a directed simple graph.
        n: Number of random payment trials.
        amount_sat: Payment amount for every trial.
        seed: RNG seed for reproducible src/dst selection.
        strategy: Routing strategy (defaults to :class:`CheapestFeeRouter`).

    Returns:
        A :class:`RandomPaymentSummary` with the success rate and fee/hop distributions.
    """
    strategy = strategy or CheapestFeeRouter()
    directed = _as_directed_simple(graph)
    routable = routable_view(directed, amount_sat)

    nodes = sorted(routable.nodes())
    rng = random.Random(seed)

    results: List[PaymentResult] = []
    fees: List[int] = []
    hops: List[int] = []
    failure_reasons: Dict[str, int] = {}
    num_success = 0

    if len(nodes) < 2:
        return RandomPaymentSummary(
            trials=0,
            amount_sat=amount_sat,
            num_success=0,
            num_failure=0,
            success_rate=0.0,
        )

    for _ in range(n):
        src, dst = rng.sample(nodes, 2)
        result = _route_one(routable, src, dst, amount_sat, strategy)
        results.append(result)
        if result.success and result.route is not None:
            num_success += 1
            fees.append(result.route.total_fee_msat)
            hops.append(result.route.hops)
        else:
            reason = result.failure_reason or "unknown"
            failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    return RandomPaymentSummary(
        trials=n,
        amount_sat=amount_sat,
        num_success=num_success,
        num_failure=n - num_success,
        success_rate=num_success / n if n else 0.0,
        fees_msat=fees,
        hops=hops,
        failure_reasons=failure_reasons,
        results=results,
    )
