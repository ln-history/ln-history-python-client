"""Analytics over Lightning Network snapshot graphs.

This subpackage provides node ranking / centrality (:mod:`centrality`), payment routing
and simulation (:mod:`routing`, :mod:`payments`), distribution-concentration helpers
(:mod:`concentration`), and the single source of truth for edge-weight conventions
(:mod:`weights`).

Routing runs on the canonical ``MultiDiGraph`` and searches backwards from the
destination, so per-hop amounts — and therefore fees and htlc/balance tests — are exact.
Assign balances first with :func:`lnhistoryclient.graph.assign_balances` to make payment
simulation liquidity-aware.

Requires the ``analysis`` extra: ``pip install lnhistoryclient[analysis]``.
"""

from lnhistoryclient.analysis.activity import (
    DEFAULT_RULE,
    STRUCTURAL,
    ActivityClass,
    ActivityRule,
    NodeProfile,
    active_nodes,
    activity_curve,
    node_profiles,
    profile_frame,
    strong_core,
    summarise,
)
from lnhistoryclient.analysis.centrality import Metric, top_nodes_by
from lnhistoryclient.analysis.clients import (
    PAPER_CLIENTS,
    PAPER_VERSIONS,
    ClientProfile,
    Implementation,
    available_clients,
    client_profile,
)
from lnhistoryclient.analysis.compare import (
    ClientComparison,
    ClientOutcome,
    build_trials,
    client_router,
    compare_clients,
)
from lnhistoryclient.analysis.concentration import gini, lorenz_xy, top_pct_share
from lnhistoryclient.analysis.mincut import (
    Channel,
    MinCutSamples,
    amount_at_service_level,
    channel_table,
    exact_feasibility,
    feasibility,
    feasibility_curve,
    induced_subgraph,
    load_bos_nodes,
    professional_nodes,
    sample_min_cuts,
    service_level_table,
    survival_curve,
)
from lnhistoryclient.analysis.payments import (
    AmountDistribution,
    FixedAmount,
    LogNormalAmount,
    NodeSelection,
    PaymentResult,
    RandomPaymentSummary,
    UniformAmount,
    simulate_payment,
    simulate_random_payments,
)
from lnhistoryclient.analysis.routing import (
    ClientRouter,
    Constraint,
    Hop,
    ReverseDijkstraRouter,
    Route,
    RoutingIndex,
    RoutingStrategy,
)
from lnhistoryclient.analysis.weights import DEFAULT_AMOUNT_SAT, Weighting

__all__ = [
    "Metric",
    "top_nodes_by",
    "Weighting",
    "DEFAULT_AMOUNT_SAT",
    "gini",
    "lorenz_xy",
    "top_pct_share",
    # payments
    "simulate_payment",
    "simulate_random_payments",
    "PaymentResult",
    "RandomPaymentSummary",
    "NodeSelection",
    "AmountDistribution",
    "FixedAmount",
    "LogNormalAmount",
    "UniformAmount",
    # routing
    "RoutingStrategy",
    "ReverseDijkstraRouter",
    "RoutingIndex",
    "Constraint",
    "Route",
    "Hop",
    # client-specific pathfinding
    "ClientRouter",
    "client_profile",
    "client_router",
    "available_clients",
    "ClientProfile",
    "Implementation",
    "PAPER_CLIENTS",
    "PAPER_VERSIONS",
    "compare_clients",
    "build_trials",
    "ClientComparison",
    "ClientOutcome",
    # payment feasibility (max-flow / min-cut)
    "sample_min_cuts",
    "MinCutSamples",
    "Channel",
    "channel_table",
    "induced_subgraph",
    "feasibility",
    "feasibility_curve",
    "survival_curve",
    "amount_at_service_level",
    "service_level_table",
    "exact_feasibility",
    "load_bos_nodes",
    "professional_nodes",
    # node activity / the strong core
    "node_profiles",
    "strong_core",
    "NodeProfile",
    "ActivityClass",
    "ActivityRule",
    "DEFAULT_RULE",
    "STRUCTURAL",
    "active_nodes",
    "activity_curve",
    "profile_frame",
    "summarise",
]
