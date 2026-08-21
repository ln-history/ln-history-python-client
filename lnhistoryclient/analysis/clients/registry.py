"""Resolve ``(implementation, version, variant)`` to the behaviour that version shipped.

Client pathfinding is not static. Between 2022 and 2026 LDK turned its live liquidity
penalty off by default and swapped its density function; eclair deleted a whole weighting
mode; LND changed which amount the timelock penalty is charged against. Comparing a 2021
snapshot against today's constants would measure the wrong network *and* the wrong client,
so every profile here is pinned to a release range with a cited reason for its boundary.

Resolution picks the newest release whose ``since`` is at or below the requested version,
which is the same rule a "what did my node do then" question implies. Requesting a version
older than anything modelled raises rather than silently extrapolating backwards.

The default version is the one the paper studied
------------------------------------------------
``client_profile("lnd")`` resolves to LND v0.17.4-beta, not to the newest release
modelled. A moving default would make a study's results change when this library is
upgraded; a pinned one keeps ``client_profile("lnd")`` reproducible and citable. Ask for a
version explicitly — or :data:`LATEST` — to track anything else.
"""

from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

from lnhistoryclient.analysis.clients.base import (
    Algorithm,
    ClientProfile,
    FeeBudget,
    Implementation,
    PathConstraints,
    WeightFunction,
)
from lnhistoryclient.analysis.clients.cln import ROUTING_MAX_HOPS, ClnWeightFunction
from lnhistoryclient.analysis.clients.eclair import (
    DEFAULT_MAX_FEE_FLAT_SAT,
    DEFAULT_MAX_FEE_PROPORTIONAL_PERCENT,
    DEFAULT_MAX_ROUTE_LENGTH,
    DEFAULT_ROUTE_MAX_CLTV,
    DEFAULT_ROUTES_COUNT,
    ROUTE_MAX_LENGTH,
    EclairConstantsWeightFunction,
    EclairRatiosWeightFunction,
)
from lnhistoryclient.analysis.clients.ldk import (
    DEFAULT_MAX_TOTAL_CLTV_EXPIRY_DELTA,
    LDK_V0_1_PARAMETERS,
    MAX_PATH_LENGTH_ESTIMATE,
    MEDIAN_HOP_CLTV_EXPIRY_DELTA,
    LdkScoringParameters,
    LdkWeightFunction,
)
from lnhistoryclient.analysis.clients.lnd import (
    DEFAULT_MIN_ROUTE_PROBABILITY,
    AprioriEstimator,
    BimodalEstimator,
    LndWeightFunction,
    UniformEstimator,
)

#: Ask for the newest release this library models, accepting that it may move.
LATEST = "latest"

#: A BOLT #11 invoice's default ``min_final_cltv_expiry``. Clients subtract it from their
#: total CLTV budget before searching, so it shifts every timelock constraint below.
DEFAULT_FINAL_CLTV_EXPIRY_DELTA = 18

#: The exact releases evaluated in arXiv:2410.13784v2, Section VII.
PAPER_VERSIONS: Dict[Implementation, str] = {
    Implementation.LND: "0.17.4",
    Implementation.CLN: "24.02.1",
    Implementation.LDK: "0.0.120",
    Implementation.ECLAIR: "0.10.0",
}


def parse_version(text: str) -> Tuple[int, ...]:
    """Parse ``"v0.17.4-beta"`` / ``"24.02.1"`` into a comparable tuple.

    Trailing qualifiers are dropped: a pre-release is treated as its release, because no
    client changes its weight function between ``-beta`` and final.
    """
    parts: List[int] = []
    for chunk in text.strip().lower().lstrip("v").split("."):
        digits = ""
        for character in chunk:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
    if not parts:
        raise ValueError(f"could not parse a version from {text!r}")
    return tuple(parts)


@dataclass(frozen=True)
class Release:
    """One span of releases that share a pathfinding behaviour."""

    since: Tuple[int, ...]
    #: Representative version reported in :attr:`ClientProfile.version`.
    label: str
    variants: Dict[str, Callable[[], WeightFunction]]
    default_variant: str
    constraints: PathConstraints
    algorithm: Algorithm = Algorithm.DIJKSTRA
    k_paths: int = 1
    relaxed_constraints: Optional[PathConstraints] = None
    notes: str = ""
    sources: Tuple[str, ...] = ()


# ── LND ──────────────────────────────────────────────────────────────────────────

_LND_CONSTRAINTS = PathConstraints(
    # RestrictParams.CltvLimit = MaxOutgoingCltvExpiry - final_cltv - BlockPadding(3).
    max_cltv_expiry_delta=2016 - DEFAULT_FINAL_CLTV_EXPIRY_DELTA - 3,
    min_path_probability=DEFAULT_MIN_ROUTE_PROBABILITY,
    # lnwallet.DefaultRoutingFeeLimitForAmount: 5%, but 100% at or below 1000 sat.
    fee_budget=FeeBudget(ppm=50_000, full_amount_below_msat=1_000_000),
    # There is no explicit hop limit; onion payload size caps routes near 20-27 hops.
    max_path_length=None,
    enforce_during_search=True,
)

_LND_VARIANTS: Dict[str, Callable[[], WeightFunction]] = {
    "apriori": lambda: LndWeightFunction(AprioriEstimator()),
    "bimodal": lambda: LndWeightFunction(BimodalEstimator()),
    "uniform": lambda: LndWeightFunction(UniformEstimator()),
}

_LND_RELEASES = (
    Release(
        since=(0, 16, 0),
        label="0.17.4",
        variants=_LND_VARIANTS,
        default_variant="apriori",
        constraints=_LND_CONSTRAINTS,
        algorithm=Algorithm.MODIFIED_DIJKSTRA,
        notes=(
            "Paper baseline. The bimodal estimator and the apriori capacity factor both "
            "arrived in v0.16.0. Mission Control history is not modelled, so both "
            "estimators return their cold-start value."
        ),
        sources=("routing/pathfind.go:275 edgeWeight", "routing/probability_apriori.go:268 capacityFactor"),
    ),
    Release(
        since=(0, 18, 0),
        label="0.18.0",
        variants=_LND_VARIANTS,
        default_variant="apriori",
        constraints=_LND_CONSTRAINTS,
        algorithm=Algorithm.MODIFIED_DIJKSTRA,
        notes=(
            "v0.18 added inbound fees to pathfinding, which BOLT #7 gossip does not carry "
            "and which this library therefore ignores. Otherwise identical to v0.17."
        ),
        sources=("routing/pathfind.go inboundFee",),
    ),
    Release(
        since=(0, 19, 0),
        label="0.19.0",
        variants={
            "apriori": lambda: LndWeightFunction(AprioriEstimator(), locked_amount_includes_fee=False),
            "bimodal": lambda: LndWeightFunction(BimodalEstimator(), locked_amount_includes_fee=False),
            "uniform": lambda: LndWeightFunction(UniformEstimator(), locked_amount_includes_fee=False),
        },
        default_variant="apriori",
        constraints=_LND_CONSTRAINTS,
        algorithm=Algorithm.MODIFIED_DIJKSTRA,
        notes=(
            "v0.19 charges the timelock penalty against amountToSend rather than "
            "amountToReceive. Its bimodal regularisation term is NOT modelled, so the "
            "bimodal variant here still behaves as v0.16-v0.18."
        ),
        sources=("routing/pathfind.go edgeWeight(amountToSend, ...)",),
    ),
)


# ── Core Lightning ───────────────────────────────────────────────────────────────

_CLN_RELEASES = (
    Release(
        since=(0, 10, 0),
        label="24.02.1",
        variants={
            "pay": lambda: ClnWeightFunction(),
            "getroute": lambda: ClnWeightFunction(use_capacity_bias=False),
        },
        default_variant="pay",
        # ROUTING_MAX_HOPS is checked on the finished path; a too-long route triggers a
        # re-run with a length-first scorer rather than an immediate failure.
        constraints=PathConstraints(max_path_length=ROUTING_MAX_HOPS, enforce_during_search=False),
        notes=(
            "One profile spans v0.10 through v26.04: route_score is byte-identical across "
            "that range. askrene shipped in v24.08 but only took over `pay` in v26.06, and "
            "its min-cost-flow engine is not modelled here. The `getroute` variant drops "
            "the capacity bias, matching route_score_cheaper which that RPC actually uses."
        ),
        sources=("plugins/libplugin-pay.c route_score", "common/dijkstra.c:106 risk_price"),
    ),
)


# ── LDK ──────────────────────────────────────────────────────────────────────────

# router.rs budgets intermediate hops at max_total - final_cltv - 2 * MEDIAN_HOP.
_LDK_CONSTRAINTS = PathConstraints(
    max_cltv_expiry_delta=(
        DEFAULT_MAX_TOTAL_CLTV_EXPIRY_DELTA - DEFAULT_FINAL_CLTV_EXPIRY_DELTA - 2 * MEDIAN_HOP_CLTV_EXPIRY_DELTA
    ),
    # There is no path-probability constraint in LDK; the paper's is a misread per-hop clamp.
    min_path_probability=None,
    max_path_length=MAX_PATH_LENGTH_ESTIMATE,
    fee_budget=FeeBudget(base_msat=50_000, ppm=10_000),
    enforce_during_search=True,
)

_LDK_RELEASES = (
    Release(
        since=(0, 0, 117),
        label="0.0.120",
        variants={
            "default": lambda: LdkWeightFunction(LdkScoringParameters()),
            "linear": lambda: LdkWeightFunction(replace(LdkScoringParameters(), probability_model="linear")),
        },
        default_variant="default",
        constraints=_LDK_CONSTRAINTS,
        notes=(
            "Paper baseline. v0.0.117 introduced the nonlinear (cubic) success probability "
            "and made it the default; `linear` forces the older uniform branch, which is "
            "the paper's LDK-un. Historical liquidity buckets cannot be populated from a "
            "snapshot, so the historical term always takes LDK's no-data fallback."
        ),
        sources=("lightning/src/routing/scoring.rs success_probability", "routing/router.rs add_entry!"),
    ),
    Release(
        since=(0, 1, 0),
        label="0.1.0",
        variants={
            "default": lambda: LdkWeightFunction(LDK_V0_1_PARAMETERS),
            "linear": lambda: LdkWeightFunction(replace(LDK_V0_1_PARAMETERS, probability_model="linear")),
        },
        default_variant="default",
        constraints=_LDK_CONSTRAINTS,
        notes=(
            "v0.1.0 turned the live liquidity penalty OFF by default "
            "(liquidity_penalty_multiplier_msat = 0), raised the base penalty 500 -> 1024 "
            "msat, and replaced the cubic density with a degree-9 one scaled by 2^36. "
            "Conclusions drawn from the paper's LDK numbers do not carry over to this range."
        ),
        sources=("scoring.rs nonlinear_success_probability", "CHANGELOG 0.1 #3368, #3495"),
    ),
)


# ── eclair ───────────────────────────────────────────────────────────────────────

_ECLAIR_FEE_BUDGET = FeeBudget(
    base_msat=DEFAULT_MAX_FEE_FLAT_SAT * 1000,
    ppm=DEFAULT_MAX_FEE_PROPORTIONAL_PERCENT * 10_000,
    combine="max",
)


def _eclair_constraints(max_cltv: int) -> PathConstraints:
    return PathConstraints(
        max_cltv_expiry_delta=max_cltv,
        max_path_length=DEFAULT_MAX_ROUTE_LENGTH,
        fee_budget=_ECLAIR_FEE_BUDGET,
        enforce_during_search=True,
    )


def _eclair_relaxed(max_cltv: int) -> PathConstraints:
    """The second pass: eclair widens to its hard ceilings when the first finds nothing."""
    return PathConstraints(
        max_cltv_expiry_delta=max_cltv,
        max_path_length=ROUTE_MAX_LENGTH,
        fee_budget=_ECLAIR_FEE_BUDGET,
        enforce_during_search=True,
    )


_ECLAIR_ALL_VARIANTS: Dict[str, Callable[[], WeightFunction]] = {
    "ratios": lambda: EclairRatiosWeightFunction(),
    "constants": lambda: EclairConstantsWeightFunction(use_log_probability=False),
    "constants-log": lambda: EclairConstantsWeightFunction(use_log_probability=True),
}

_ECLAIR_RELEASES = (
    Release(
        since=(0, 6, 2),
        label="0.6.2",
        variants=_ECLAIR_ALL_VARIANTS,
        default_variant="ratios",
        constraints=_eclair_constraints(1008),
        relaxed_constraints=_eclair_relaxed(1008),
        algorithm=Algorithm.YEN,
        k_paths=DEFAULT_ROUTES_COUNT,
        notes="v0.6.2 introduced hop costs and the nested path-finding config. max-cltv was still 1008.",
        sources=("eclair-core/src/main/resources/reference.conf",),
    ),
    Release(
        since=(0, 9, 0),
        label="0.10.0",
        variants=_ECLAIR_ALL_VARIANTS,
        default_variant="ratios",
        constraints=_eclair_constraints(DEFAULT_ROUTE_MAX_CLTV),
        relaxed_constraints=_eclair_relaxed(DEFAULT_ROUTE_MAX_CLTV),
        algorithm=Algorithm.YEN,
        k_paths=DEFAULT_ROUTES_COUNT,
        notes=(
            "Paper baseline; v0.9.0 raised max-cltv from 1008 to 2016. The three variants "
            "are the paper's Eclair1/2/3. eclair picks randomly among its K candidates when "
            "randomize-route-selection is on; this returns them ranked instead, so results "
            "stay deterministic."
        ),
        sources=("router/Graph.scala:309 RichWeight", "reference.conf path-finding.default"),
    ),
    Release(
        since=(0, 13, 1),
        label="0.13.1",
        variants={
            "constants": lambda: EclairConstantsWeightFunction(use_log_probability=False),
            "constants-log": lambda: EclairConstantsWeightFunction(use_log_probability=True),
        },
        default_variant="constants",
        constraints=_eclair_constraints(DEFAULT_ROUTE_MAX_CLTV),
        relaxed_constraints=_eclair_relaxed(DEFAULT_ROUTE_MAX_CLTV),
        algorithm=Algorithm.YEN,
        k_paths=DEFAULT_ROUTES_COUNT,
        notes=(
            "v0.13.1 removed the ratios mode for payments entirely (commit fa1b0eef5), "
            "making the constants form the only option. Asking for `ratios` at or after "
            "this version is an error rather than a silent fallback."
        ),
        sources=("commit fa1b0eef5 'Remove PaymentWeightRatios from the routing config'",),
    ),
)


_RELEASES: Dict[Implementation, Tuple[Release, ...]] = {
    Implementation.LND: _LND_RELEASES,
    Implementation.CLN: _CLN_RELEASES,
    Implementation.LDK: _LDK_RELEASES,
    Implementation.ECLAIR: _ECLAIR_RELEASES,
}


# ── resolution ───────────────────────────────────────────────────────────────────


def parse_client_spec(spec: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Split ``"eclair-constants-log@0.13.1"`` into ``("eclair", "0.13.1", "constants-log")``.

    Only the first ``-`` separates implementation from variant, so multi-word variant
    names survive intact.
    """
    name, _, version = spec.strip().partition("@")
    implementation, _, variant = name.strip().lower().partition("-")
    return implementation, version.strip() or None, variant or None


def _select_release(implementation: Implementation, version: Optional[str]) -> Release:
    releases = _RELEASES[implementation]
    if version is None:
        version = PAPER_VERSIONS[implementation]
    elif version == LATEST:
        return releases[-1]

    requested = parse_version(version)
    chosen: Optional[Release] = None
    for release in releases:
        if requested >= release.since:
            chosen = release
    if chosen is None:
        oldest = ".".join(str(part) for part in releases[0].since)
        raise ValueError(
            f"{implementation.value} {version} predates the oldest modelled release ({oldest}). "
            "Pathfinding before that point differed in ways this library does not reproduce; "
            "pin an explicit later version rather than extrapolating backwards."
        )
    return chosen


def client_profile(
    implementation: str,
    version: Optional[str] = None,
    variant: Optional[str] = None,
) -> ClientProfile:
    """Build the profile for a client, version and variant.

    Args:
        implementation: ``"lnd"``, ``"cln"``, ``"ldk"``, ``"eclair"`` — or a full spec
            string such as ``"eclair-constants-log@0.13.1"``, in which case the other two
            arguments are only used as fallbacks.
        version: Release to model. Defaults to the version the paper evaluated (see
            :data:`PAPER_VERSIONS`); pass :data:`LATEST` for the newest modelled.
        variant: Configuration within that release, e.g. ``"bimodal"`` for LND or
            ``"ratios"`` for eclair. Defaults to the release's own default.

    Returns:
        A :class:`~...base.ClientProfile` ready for
        :class:`~lnhistoryclient.analysis.routing.ClientRouter`.

    Raises:
        ValueError: For an unknown implementation or variant, or a version older than
            anything modelled. Variants are never silently substituted, because quietly
            routing "eclair ratios at v0.14" as something else would fabricate a result.
    """
    name, spec_version, spec_variant = parse_client_spec(implementation)
    version = spec_version or version
    variant = spec_variant or variant

    try:
        resolved = Implementation(name)
    except ValueError:
        known = ", ".join(item.value for item in Implementation)
        raise ValueError(f"unknown implementation {name!r}; expected one of: {known}") from None

    release = _select_release(resolved, version)
    chosen_variant = variant or release.default_variant
    if chosen_variant not in release.variants:
        available = ", ".join(sorted(release.variants))
        raise ValueError(f"{resolved.value} {release.label} has no variant {chosen_variant!r}; available: {available}")

    return ClientProfile(
        implementation=resolved,
        version=release.label,
        variant=chosen_variant,
        weight=release.variants[chosen_variant](),
        constraints=release.constraints,
        algorithm=release.algorithm,
        k_paths=release.k_paths,
        relaxed_constraints=release.relaxed_constraints,
        notes=release.notes,
        sources=release.sources,
    )


def available_clients() -> List[Dict[str, Any]]:
    """Every modelled release, for discovery and for citing what a study actually ran."""
    return [
        {
            "implementation": implementation.value,
            "since": ".".join(str(part) for part in release.since),
            "version": release.label,
            "is_paper_baseline": release.label == PAPER_VERSIONS[implementation],
            "variants": sorted(release.variants),
            "default_variant": release.default_variant,
            "algorithm": release.algorithm.value,
            "notes": release.notes,
        }
        for implementation, releases in _RELEASES.items()
        for release in releases
    ]


#: The nine client variants benchmarked in the paper, as spec strings. Passing this to
#: ``compare_clients`` reproduces the comparison in its Tables III-VIII.
PAPER_CLIENTS: Tuple[str, ...] = (
    "lnd-apriori@0.17.4",
    "lnd-bimodal@0.17.4",
    "lnd-uniform@0.17.4",
    "cln@24.02.1",
    "ldk-linear@0.0.120",
    "ldk@0.0.120",
    "eclair-ratios@0.10.0",
    "eclair-constants@0.10.0",
    "eclair-constants-log@0.10.0",
)
