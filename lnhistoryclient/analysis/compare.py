"""Run one set of payments through several client implementations and compare them.

The comparison is **paired**: every client is handed the identical list of
``(src, dst, amount)`` trials against the identical graph state, so a difference in the
results is a difference in pathfinding and nothing else. Sampling each client
independently would confound the clients with the draw, which on a network this skewed is
easily the larger effect.

Which trials the aggregates use
-------------------------------
Success rate is reported over **all** trials — that is the metric of interest. Fee, hop
count and timelock are reported over the trials where *every* client succeeded, because a
client that only routes the easy pairs would otherwise look cheap by having declined the
expensive ones. The paper applies the same rule (Section VII-B, "we exclusively considered
transactions for which routing succeeded in five or more routing client variants"); this
is the stricter all-clients version of it, and :attr:`ClientComparison.common_success`
exposes exactly which trials it covers.

Balances are never committed, so the order of clients cannot matter.
"""

import logging
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import networkx as nx
import numpy as np

from lnhistoryclient.analysis.clients.base import ClientProfile
from lnhistoryclient.analysis.clients.registry import PAPER_CLIENTS, client_profile
from lnhistoryclient.analysis.payments import (
    AmountDistribution,
    FixedAmount,
    PaymentResult,
    _constraints_for,
    _require_multidigraph,
    _route_one,
)
from lnhistoryclient.analysis.routing import ClientRouter, Constraint, Route, RoutingIndex
from lnhistoryclient.analysis.weights import DEFAULT_AMOUNT_SAT

logger = logging.getLogger(__name__)

#: One trial: sender, recipient, amount in satoshis.
Trial = Tuple[str, str, int]


def client_router(client: Union[str, ClientProfile]) -> ClientRouter:
    """Build a router from a spec string such as ``"lnd-bimodal@0.18.0"``, or a profile."""
    profile = client if isinstance(client, ClientProfile) else client_profile(client)
    return ClientRouter(profile)


@dataclass
class ClientOutcome:
    """One client's results over the shared trial set."""

    label: str
    profile: Dict[str, Any]
    trials: int
    num_success: int
    success_rate: float
    results: List[PaymentResult] = field(default_factory=list)
    failure_reasons: Dict[str, int] = field(default_factory=dict)

    def metrics_over(self, indices: Sequence[int]) -> Dict[str, Any]:
        """Fee/length/timelock aggregates restricted to the given trials.

        ``fee_ratio_pct`` is the median of per-payment ``fee / amount``, matching the
        paper's Table IV. A median of ratios, not a ratio of totals: the amounts span
        orders of magnitude, so totals would be dominated by the largest payment.
        """
        routes: List[Route] = []
        for position in indices:
            route = self.results[position].route
            if route is not None:
                routes.append(route)
        if not routes:
            return {"fee_ratio_pct": None, "avg_path_length": None, "avg_timelock": None, "n": 0}

        ratios = [route.total_fee_msat / (route.amount_sat * 1000) * 100 for route in routes if route.amount_sat]
        return {
            "fee_ratio_pct": median(ratios) if ratios else None,
            "avg_path_length": sum(route.num_hops for route in routes) / len(routes),
            "avg_timelock": sum(route.total_cltv for route in routes) / len(routes),
            "median_fee_msat": median(route.total_fee_msat for route in routes),
            "n": len(routes),
        }


@dataclass
class ClientComparison:
    """Paired results for several clients over one trial set."""

    trials: List[Trial]
    outcomes: List[ClientOutcome]
    #: Indices of trials every client routed — the basis for the cost comparison.
    common_success: List[int] = field(default_factory=list)

    @property
    def num_trials(self) -> int:
        return len(self.trials)

    def summary(self) -> List[Dict[str, Any]]:
        """One row per client: success rate over all trials, costs over common successes."""
        return [
            {
                "client": outcome.label,
                "success_rate": outcome.success_rate,
                "num_success": outcome.num_success,
                "trials": outcome.trials,
                **outcome.metrics_over(self.common_success),
                "failure_reasons": dict(outcome.failure_reasons),
            }
            for outcome in self.outcomes
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "num_trials": self.num_trials,
            "num_common_success": len(self.common_success),
            "clients": [outcome.profile for outcome in self.outcomes],
            "summary": self.summary(),
        }

    def to_frame(self) -> Any:
        """``pandas.DataFrame`` of :meth:`summary`. Requires the ``analysis`` extra."""
        import pandas as pd

        return pd.DataFrame(self.summary())

    def route_frame(self) -> Any:
        """One row per ``(trial, client)`` — the tidy form of the whole comparison.

        ``route_key`` is the ordered channel list, so two clients agree on a trial exactly
        when their ``route_key`` matches. It is built from ``scid``s rather than node ids
        because two routes over the same nodes but different parallel channels are
        genuinely different routes, and one of them may be the reason a client is cheaper.

        Rows for failed trials are kept, with the route columns null. Dropping them would
        make ``groupby(client)`` silently compare different trial sets, which is the
        mistake the paired design exists to prevent.
        """
        import pandas as pd

        rows: List[Dict[str, Any]] = []
        for outcome in self.outcomes:
            for position, result in enumerate(outcome.results):
                route = result.route
                row: Dict[str, Any] = {
                    "trial": position,
                    "client": outcome.label,
                    "src": result.src,
                    "dst": result.dst,
                    "amount_sat": result.amount_sat,
                    "success": result.success,
                    "failure_reason": result.failure_reason,
                }
                if route is None:
                    row.update(
                        fee_msat=None,
                        num_hops=None,
                        total_cltv=None,
                        success_probability=None,
                        total_weight=None,
                        route_key=None,
                    )
                else:
                    row.update(
                        fee_msat=route.total_fee_msat,
                        num_hops=route.num_hops,
                        total_cltv=route.total_cltv,
                        success_probability=route.success_probability,
                        total_weight=route.total_weight,
                        route_key="-".join(str(hop.scid) for hop in route.hops),
                    )
                rows.append(row)
        return pd.DataFrame(rows)

    def hop_frame(self) -> Any:
        """One row per ``(trial, client, hop)``, for per-node and per-channel ledgers.

        ``fee_msat`` is credited to ``src``, the node doing the forwarding, and is zero on
        the first hop because the sender originates rather than forwards.
        """
        import pandas as pd

        rows: List[Dict[str, Any]] = []
        for outcome in self.outcomes:
            for position, result in enumerate(outcome.results):
                if result.route is None:
                    continue
                for order, hop in enumerate(result.route.hops):
                    rows.append(
                        {
                            "trial": position,
                            "client": outcome.label,
                            "hop_index": order,
                            "src": hop.src,
                            "dst": hop.dst,
                            "scid": hop.scid,
                            "amount_forwarded_msat": hop.amount_forwarded_msat,
                            "fee_msat": hop.fee_msat,
                            "cltv_expiry_delta": hop.cltv_expiry_delta,
                            "capacity_sat": hop.capacity_sat,
                        }
                    )
        return pd.DataFrame(rows)


def build_trials(
    graph: nx.MultiDiGraph,
    n: int,
    amount_sat: Union[int, AmountDistribution] = DEFAULT_AMOUNT_SAT,
    seed: Optional[int] = None,
    nodes: Optional[Iterable[str]] = None,
) -> List[Trial]:
    """Draw ``n`` random ``(src, dst, amount)`` trials from nodes that have channels.

    Nodes with no channels are excluded: they can neither send nor receive, and on a real
    snapshot they are a large enough share of the node set to swamp every rate with
    guaranteed failures.

    Pass ``nodes`` to draw from a restricted pool instead —
    :meth:`RoutingIndex.routable_core` is the usual choice, and turns the trial set from
    "can this pair be paid at all" into "which route does each client pick".
    """
    _require_multidigraph(graph)
    if nodes is not None:
        pool = sorted(set(nodes) & set(graph.nodes()))
    else:
        pool = sorted(node for node in graph.nodes() if graph.degree(node) > 0)
    if len(pool) < 2 or n <= 0:
        return []

    rng = np.random.default_rng(seed)
    distribution = amount_sat if isinstance(amount_sat, AmountDistribution) else FixedAmount(int(amount_sat))
    amounts = distribution.sample(n, rng)

    trials: List[Trial] = []
    for index in range(n):
        picked = rng.choice(len(pool), size=2, replace=False)
        trials.append((pool[int(picked[0])], pool[int(picked[1])], int(amounts[index])))
    return trials


def compare_clients(
    graph: nx.MultiDiGraph,
    clients: Iterable[Union[str, ClientProfile]] = PAPER_CLIENTS,
    *,
    n: int = 500,
    amount_sat: Union[int, AmountDistribution] = DEFAULT_AMOUNT_SAT,
    seed: Optional[int] = None,
    trials: Optional[Sequence[Trial]] = None,
    constraints: Optional[Constraint] = None,
    diagnose: bool = True,
) -> ClientComparison:
    """Route the same payments with each client and compare the outcomes.

    Args:
        graph: Canonical ``MultiDiGraph`` from ``build_multidigraph``. Enrich it with real
            capacities first — every client except CLN's ``getroute`` variant prices
            capacity, and the ``htlc_maximum_msat`` proxy will distort those terms.
        clients: Spec strings (``"lnd-bimodal@0.18.0"``) or prebuilt profiles. Defaults to
            the nine variants the paper benchmarked.
        n: Number of trials to draw when ``trials`` is not given.
        amount_sat: Fixed amount, or an :class:`AmountDistribution` to sample per trial.
        seed: Seed for trial selection.
        trials: Explicit ``(src, dst, amount)`` list, bypassing sampling. Use this to
            compare against a fixed pair set across snapshots.
        constraints: Hard-constraint override. Defaults to everything, minus ``BALANCE``
            when the graph has no balances assigned.
        diagnose: Attribute each failure via the relaxation ladder.

    Returns:
        A :class:`ClientComparison`.
    """
    _require_multidigraph(graph)
    trial_list = list(trials) if trials is not None else build_trials(graph, n, amount_sat, seed)
    profiles = [item if isinstance(item, ClientProfile) else client_profile(item) for item in clients]
    if not profiles:
        raise ValueError("compare_clients needs at least one client")

    # Built once and shared: it is pure structure, and every client reads the same graph.
    index = RoutingIndex(graph)
    effective = _constraints_for(graph) if constraints is None else constraints

    outcomes: List[ClientOutcome] = []
    for profile in profiles:
        router = ClientRouter(profile)
        results: List[PaymentResult] = []
        reasons: Dict[str, int] = {}
        successes = 0

        for src, dst, amount in trial_list:
            result = _route_one(graph, index, src, dst, amount, router, effective, commit=False, diagnose=diagnose)
            results.append(result)
            if result.success:
                successes += 1
            else:
                reason = result.failure_reason or "unknown"
                reasons[reason] = reasons.get(reason, 0) + 1

        outcomes.append(
            ClientOutcome(
                label=profile.label,
                profile=profile.describe(),
                trials=len(trial_list),
                num_success=successes,
                success_rate=successes / len(trial_list) if trial_list else 0.0,
                results=results,
                failure_reasons=reasons,
            )
        )
        logger.info("compare_clients: %s routed %d/%d", profile.label, successes, len(trial_list))

    common = [
        position
        for position in range(len(trial_list))
        if all(outcome.results[position].success for outcome in outcomes)
    ]
    if trial_list and not common:
        logger.warning(
            "compare_clients: no trial succeeded for every client, so fee/length/timelock "
            "comparisons are empty. Success rates are still valid."
        )
    return ClientComparison(trials=trial_list, outcomes=outcomes, common_success=common)
