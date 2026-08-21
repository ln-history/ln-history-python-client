"""eclair's pathfinding cost — paper Section VI-D, verified against ``router/Graph.scala``.

eclair is the only client with genuinely different *shapes* of weight function, selected
by config rather than by version:

* **ratios** (paper Case 1) — ``(fee + hop_cost) * factor``, where ``factor`` blends
  normalised timelock, channel age and capacity. The default up to v0.13.0.
* **constants** (Case 2) — ``fees + hop_cost + risk_cost + failure_cost / P_path``.
  Note the division is by the **cumulative path** probability, so this one is *not*
  additive over edges; it has the same additive-plus-multiplicative shape as LND's.
* **constants-log** (Case 3) — ``fees + hop_cost + risk_cost - failure_cost * ln(P_e)``,
  which *is* additive. The paper found this variant gave the lowest fee ratios of any
  client tested.

Corrections to the paper
------------------------
* **The ratio defaults are stale by about three years.** The paper's ``age 0.35 /
  base 0 / capacity 0.5 / cltv 0.15`` are eclair **<= v0.5.1** values. From v0.6.0 the
  defaults are ``age 0.4 / base 0.0 / capacity 0.55 / cltv 0.05``, which is what v0.10.0
  — the version the paper claims to model — actually ships.
* **Hop costs are not zero.** ``reference.conf`` has ``hop-cost.fee-base-msat = 500`` and
  ``fee-proportional-millionths = 200``, and has since v0.6.2. Zeroing them removes
  eclair's entire structural preference for short paths.
* **The default max route length is 6, not 20.** ``ROUTE_MAX_LENGTH = 20`` is only the
  ceiling used on the *retry* after the first pass finds nothing — modelled here through
  :attr:`~...base.ClientProfile.relaxed_constraints`.
* Boundaries are enforced **during** the search as a pruning predicate, not validated
  afterwards as Table I's asterisk suggests.

Capacity is a hard constraint here, unlike in CLN: ``dijkstraShortestPath`` filters on
``amount <= capacity`` before any scoring.
"""

import math
from typing import Any, Dict

from lnhistoryclient.analysis.clients.base import EdgeContext, NetworkStats, WeightFunction

# Graph.RoutingHeuristics, Graph.scala:405-429. eclair's year is 365*24*6 = 52560 blocks —
# CLN's is 52596. They are genuinely different constants; do not unify them.
BLOCK_TIME_ONE_YEAR = 52560
CAPACITY_CHANNEL_LOW_MSAT = 100_000_000  # 1 mBTC
CAPACITY_CHANNEL_HIGH_MSAT = 100_000_000_000  # 1 BTC
CLTV_LOW = 9
CLTV_HIGH = 2016

# reference.conf, eclair.router.path-finding.default.*  (v0.6.2 onward)
DEFAULT_HOP_COST_BASE_MSAT = 500
DEFAULT_HOP_COST_PPM = 200
DEFAULT_FAILURE_COST_BASE_MSAT = 2000
DEFAULT_FAILURE_COST_PPM = 500
DEFAULT_LOCKED_FUNDS_RISK = 1e-8

# Ratio defaults as of v0.6.0 through v0.13.0 (the mode was removed in v0.13.1).
DEFAULT_RATIO_BASE = 0.0
DEFAULT_RATIO_CLTV = 0.05
DEFAULT_RATIO_CHANNEL_AGE = 0.4
DEFAULT_RATIO_CHANNEL_CAPACITY = 0.55

# RouteCalculation.scala — boundaries and the relaxed retry ceiling.
DEFAULT_MAX_ROUTE_LENGTH = 6
ROUTE_MAX_LENGTH = 20
DEFAULT_ROUTE_MAX_CLTV = 2016
DEFAULT_ROUTES_COUNT = 3
DEFAULT_MAX_FEE_FLAT_SAT = 21
DEFAULT_MAX_FEE_PROPORTIONAL_PERCENT = 3


def normalize(value: float, low: float, high: float) -> float:
    """eclair's ``RoutingHeuristics.normalize`` (``Graph.scala:422``), verbatim.

    Clamps into ``[low, high]`` then maps onto ``[0.00001, 0.99999]`` rather than
    ``[0, 1]``, so a factor can never be exactly zero and thus never cancel a whole
    weight term.
    """
    if high <= low:
        return 0.00001
    clamped = min(max(value, low), high)
    return 0.00001 + 0.99998 * (clamped - low) / (high - low)


class _EclairWeightFunction(WeightFunction):
    """Shared plumbing: the chain tip (for channel age) and the payment amount."""

    def __init__(self) -> None:
        self._tip_block_height = 0
        self._amount_msat = 0

    def prepare(self, stats: NetworkStats, amount_msat: int) -> None:
        self._tip_block_height = stats.tip_block_height
        self._amount_msat = amount_msat

    def edge_probability(self, ctx: EdgeContext) -> float:
        """``1 - amount / capacity`` (``Graph.scala:338``), the uniform-liquidity estimate.

        eclair returns exactly ``1.0`` when it knows the channel's real balance; a snapshot
        never does, so the estimate always applies here. An unknown capacity also yields
        ``1.0`` rather than penalising the channel for missing data.
        """
        capacity = ctx.capacity_msat
        if not capacity:
            return 1.0
        # Clamped away from 0 so the logarithm in the Case 3 variant stays finite.
        return min(1.0, max(1e-12, 1.0 - ctx.amount_msat / capacity))


class EclairRatiosWeightFunction(_EclairWeightFunction):
    """Case 1: ``(fee + hop_cost) * factor``, blending timelock, age and capacity.

    Default for eclair v0.6.0 through v0.13.0. Removed outright in v0.13.1 (commit
    ``fa1b0eef5``), which forced every node onto the constants form.
    """

    name = "eclair-ratios"

    def __init__(
        self,
        *,
        base_ratio: float = DEFAULT_RATIO_BASE,
        cltv_ratio: float = DEFAULT_RATIO_CLTV,
        age_ratio: float = DEFAULT_RATIO_CHANNEL_AGE,
        capacity_ratio: float = DEFAULT_RATIO_CHANNEL_CAPACITY,
        hop_cost_base_msat: int = DEFAULT_HOP_COST_BASE_MSAT,
        hop_cost_ppm: int = DEFAULT_HOP_COST_PPM,
    ) -> None:
        super().__init__()
        self.base_ratio = base_ratio
        self.cltv_ratio = cltv_ratio
        self.age_ratio = age_ratio
        self.capacity_ratio = capacity_ratio
        self.hop_cost_base_msat = hop_cost_base_msat
        self.hop_cost_ppm = hop_cost_ppm

    def edge_weight(self, ctx: EdgeContext) -> float:
        # Graph.scala:354 — the sender's own channel contributes nothing at all, in every
        # eclair mode. It charges neither fee nor hop cost, so it cannot be scored.
        if ctx.is_sender_hop:
            return 0.0

        hop_cost = self.hop_cost_base_msat + (ctx.amount_msat * self.hop_cost_ppm) // 1_000_000

        cltv_factor = normalize(ctx.cltv_expiry_delta, CLTV_LOW, CLTV_HIGH)
        # A channel with no decodable funding block (alias scids, route hints) gets the
        # *worst* age score in eclair, not the best.
        if ctx.funding_block:
            age_factor = normalize(
                ctx.funding_block, self._tip_block_height - BLOCK_TIME_ONE_YEAR, self._tip_block_height
            )
        else:
            age_factor = 1.0
        if ctx.capacity_msat:
            capacity_factor = 1.0 - normalize(ctx.capacity_msat, CAPACITY_CHANNEL_LOW_MSAT, CAPACITY_CHANNEL_HIGH_MSAT)
        else:
            capacity_factor = 1.0

        factor = (
            self.base_ratio
            + cltv_factor * self.cltv_ratio
            + age_factor * self.age_ratio
            + capacity_factor * self.capacity_ratio
        )
        return (ctx.fee_msat + hop_cost) * factor

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "base_ratio": self.base_ratio,
            "cltv_ratio": self.cltv_ratio,
            "age_ratio": self.age_ratio,
            "capacity_ratio": self.capacity_ratio,
            "hop_cost_base_msat": self.hop_cost_base_msat,
            "hop_cost_ppm": self.hop_cost_ppm,
        }


class EclairConstantsWeightFunction(_EclairWeightFunction):
    """Cases 2 and 3: virtual costs in msat rather than dimensionless ratios.

    ``use_log_probability=False`` (Case 2, and eclair's own default) divides a failure cost
    by the **cumulative path** probability, exactly as LND divides its attempt cost — so
    the cost is additive-plus-multiplicative and is folded in :meth:`path_cost` rather than
    accumulated per edge. ``True`` (Case 3) subtracts ``failure_cost * ln(P_e)`` per edge,
    which is additive and therefore optimally solvable.
    """

    def __init__(
        self,
        *,
        use_log_probability: bool = False,
        hop_cost_base_msat: int = DEFAULT_HOP_COST_BASE_MSAT,
        hop_cost_ppm: int = DEFAULT_HOP_COST_PPM,
        failure_cost_base_msat: int = DEFAULT_FAILURE_COST_BASE_MSAT,
        failure_cost_ppm: int = DEFAULT_FAILURE_COST_PPM,
        locked_funds_risk: float = DEFAULT_LOCKED_FUNDS_RISK,
    ) -> None:
        super().__init__()
        self.use_log_probability = use_log_probability
        self.hop_cost_base_msat = hop_cost_base_msat
        self.hop_cost_ppm = hop_cost_ppm
        self.failure_cost_base_msat = failure_cost_base_msat
        self.failure_cost_ppm = failure_cost_ppm
        self.locked_funds_risk = locked_funds_risk
        self.name = "eclair-constants-log" if use_log_probability else "eclair-constants"

    def _failure_cost_msat(self, amount_msat: int) -> float:
        return float(self.failure_cost_base_msat + (amount_msat * self.failure_cost_ppm) // 1_000_000)

    def edge_weight(self, ctx: EdgeContext) -> float:
        if ctx.is_sender_hop:
            return 0.0

        # eclair prices hop and risk against totalAmount — what enters this channel,
        # including this hop's own fee — not against what leaves it.
        total_amount = ctx.amount_msat + ctx.fee_msat
        hop_cost = self.hop_cost_base_msat + (total_amount * self.hop_cost_ppm) // 1_000_000
        risk_cost = total_amount * ctx.cltv_expiry_delta * self.locked_funds_risk

        if self.use_log_probability:
            probability = self.edge_probability(ctx)
            return ctx.fee_msat + hop_cost + risk_cost - self._failure_cost_msat(total_amount) * math.log(probability)
        # Case 2 keeps the fee out of the accumulator; path_cost adds it back alongside
        # the failure cost, which needs the *whole* path's probability.
        return hop_cost + risk_cost

    def path_cost(
        self,
        additive_msat: float,
        fee_msat: int,
        path_htlc_minimum_msat: int,
        probability: float,
        attempt_cost_msat: float,
    ) -> float:
        if self.use_log_probability:
            return additive_msat
        return fee_msat + additive_msat + self._failure_cost_msat(self._amount_msat + fee_msat) / probability

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "use_log_probability": self.use_log_probability,
            "hop_cost_base_msat": self.hop_cost_base_msat,
            "hop_cost_ppm": self.hop_cost_ppm,
            "failure_cost_base_msat": self.failure_cost_base_msat,
            "failure_cost_ppm": self.failure_cost_ppm,
            "locked_funds_risk": self.locked_funds_risk,
        }
