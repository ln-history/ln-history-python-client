"""Per-implementation pathfinding: how LND, CLN, LDK and eclair each choose a route.

Every client solves "find a cheap, reliable path" differently, and the differences are
large enough to change which nodes matter and what a payment costs. This subpackage
encodes each client's weight function and side constraints as data, so a snapshot can be
routed *as a specific version of a specific client would route it*:

    from lnhistoryclient.analysis import ClientRouter, client_profile, simulate_payment

    lnd = ClientRouter(client_profile("lnd", version="0.17.4"))
    result = simulate_payment(graph, alice, bob, amount_sat=100_000, strategy=lnd)

Constants are taken from the upstream sources rather than from the paper that motivated
this work (arXiv:2410.13784v2), because several of the paper's are wrong — most
consequentially LND's attempt cost, which it states in msat when the source is in sat.
Each module's docstring lists the corrections it applies and cites the file and symbol it
was checked against.

Fidelity boundary
-----------------
What is reproduced is each client's **decision rule** on a static snapshot. What is not:
payment history, learned liquidity bounds, retry loops, and multi-part splitting. Every
success probability here is therefore a cold-start estimate, which is the same assumption
the paper's own simulation makes.
"""

from lnhistoryclient.analysis.clients.base import (
    Algorithm,
    ClientProfile,
    EdgeContext,
    FeeBudget,
    Implementation,
    NetworkStats,
    PathConstraints,
    WeightFunction,
)
from lnhistoryclient.analysis.clients.cln import ClnWeightFunction
from lnhistoryclient.analysis.clients.eclair import (
    EclairConstantsWeightFunction,
    EclairRatiosWeightFunction,
)
from lnhistoryclient.analysis.clients.ldk import LdkScoringParameters, LdkWeightFunction
from lnhistoryclient.analysis.clients.lnd import (
    AprioriEstimator,
    BimodalEstimator,
    LndProbabilityEstimator,
    LndWeightFunction,
    UniformEstimator,
)
from lnhistoryclient.analysis.clients.registry import (
    LATEST,
    PAPER_CLIENTS,
    PAPER_VERSIONS,
    Release,
    available_clients,
    client_profile,
    parse_client_spec,
    parse_version,
)

__all__ = [
    # registry — the entry point
    "client_profile",
    "available_clients",
    "PAPER_CLIENTS",
    "PAPER_VERSIONS",
    "LATEST",
    "Release",
    "parse_client_spec",
    "parse_version",
    # abstractions
    "WeightFunction",
    "ClientProfile",
    "PathConstraints",
    "FeeBudget",
    "EdgeContext",
    "NetworkStats",
    "Implementation",
    "Algorithm",
    # per-client weight functions
    "LndWeightFunction",
    "LndProbabilityEstimator",
    "AprioriEstimator",
    "BimodalEstimator",
    "UniformEstimator",
    "ClnWeightFunction",
    "LdkWeightFunction",
    "LdkScoringParameters",
    "EclairRatiosWeightFunction",
    "EclairConstantsWeightFunction",
]
