"""LDK's routing cost — paper Section VI-C, verified against ``routing/scoring.rs``.

LDK is the one client whose cost is **not a sum over edges**. ``router.rs``'s ``add_entry!``
keys its heap on

    cost = max(total_fee_msat, path_htlc_minimum_msat) + path_penalty_msat

Three separate accumulators, combined with a ``max``. That is why
:meth:`LdkWeightFunction.path_cost` and :meth:`LdkWeightFunction.path_htlc_minimum_msat`
exist at all: no additive weight function can express it.

Corrections to the paper
------------------------
* **eq. (22) is not decomposable.** Presenting it as ``max(fee_e, pathHtlcMin_e) +
  penalty_e`` per edge silently assumes the ``max`` distributes over the sum. It does not.
* **eq. (23) drops a ``max``.** The real recurrence takes
  ``m = max(downstream_accumulator, htlc_minimum_e)`` before applying fees, so the paper's
  form is correct only for the final hop.
* **eq. (32)'s scale factor is wrong for v0.0.120.** The cubic model normalises everything
  by capacity *before* cubing and scales by ``2^30``. The paper's ``64 * 1024^3 = 2^36``
  belongs to LDK v0.1.0+, which pairs it with a *degree-9* density, not a cubic one.
  Mixing the two eras misweights the ``+1`` regularisation and skews low-probability
  penalties.
* **eq. (30) is missing a ``+1``** in the denominator, and that linear branch is not the
  default — ``linear_success_probability`` has defaulted to ``false`` since v0.0.117.
* **The ``-sum log(P_e) <= -log(0.01)`` side constraint does not exist.** There is no
  path-probability constraint anywhere in ``router.rs``. What exists is a *per-hop*
  saturation, ``NEGATIVE_LOG10_UPPER_BOUND = 2``, which caps how much one hop can be
  penalised. Low-probability paths are priced, never pruned.
* The CLTV budget is ``1008 - final_cltv_delta - 2 * 40``, not a flat 1008.

The v0.1.0 discontinuity
------------------------
From LDK v0.1.0 the **live liquidity penalty is off by default**
(``liquidity_penalty_multiplier_msat = 0``): only the base, anti-probing and
*historical* penalties remain, and the base penalty rose from 500 to 1024 msat. A
simulation using v0.1+ defaults must not apply the paper's liquidity term at all.

What is not modelled
--------------------
Learned liquidity. LDK's real power is its 32x32 historical bucket tracker, which a
snapshot cannot populate. Both probability estimates here are the cold-start case
(``min_liquidity = 0``, ``max_liquidity = capacity``), and the historical term falls back
to LDK's own no-data path. The paper notes the same omission as a likely cause of the low
success rates it measured for LDK.
"""

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple

from lnhistoryclient.analysis.clients.base import EdgeContext, WeightFunction

#: ``scoring.rs`` divisors. Base penalty scales by 2^30, the liquidity terms by 2^20.
BASE_AMOUNT_PENALTY_DIVISOR = 1 << 30
AMOUNT_PENALTY_DIVISOR = 1 << 20
#: ``NEGATIVE_LOG10_UPPER_BOUND`` — one hop's -log10(P) saturates here, i.e. P floors at 1%.
NEGATIVE_LOG10_UPPER_BOUND = 2
#: Penalties are computed in units of 1/2048 of a decade.
LOG10_SCALE = 2048
#: ``PRECISION_LOWER_BOUND_DENOMINATOR`` — below a 1/64 failure chance, the hop is free.
PRECISION_LOWER_BOUND_DENOMINATOR = 64

#: ``router.rs`` — hard path-length ceiling and the CLTV budget's components.
MAX_PATH_LENGTH_ESTIMATE = 19
DEFAULT_MAX_TOTAL_CLTV_EXPIRY_DELTA = 1008
MEDIAN_HOP_CLTV_EXPIRY_DELTA = 40


@dataclass(frozen=True)
class LdkScoringParameters:
    """``ProbabilisticScoringFeeParameters``, plus the probability model of the era.

    Defaults are v0.0.117-v0.0.125 — the generation the paper studied. See
    :data:`LDK_V0_1_PARAMETERS` for the modern set, which differs enough to change
    conclusions.
    """

    base_penalty_msat: int = 500
    base_penalty_amount_multiplier_msat: int = 8192
    liquidity_penalty_multiplier_msat: int = 30_000
    liquidity_penalty_amount_multiplier_msat: int = 192
    historical_liquidity_penalty_multiplier_msat: int = 10_000
    historical_liquidity_penalty_amount_multiplier_msat: int = 64
    anti_probing_penalty_msat: int = 250
    considered_impossible_penalty_msat: int = 1_0000_0000_000
    #: ``"cubic"`` for v0.0.117-v0.0.125, ``"degree9"`` for v0.1.0+, ``"linear"`` to force
    #: the ``linear_success_probability = true`` branch.
    probability_model: str = "cubic"
    #: ``BILLIONISH``: 2^30 for the cubic era, 2^36 for the degree-9 era.
    probability_scale: int = 1 << 30
    #: ``min_zero_implies_no_successes`` rescales the denominator by this ratio.
    zero_min_penalty_numerator: int = 21
    zero_min_penalty_denominator: int = 16


#: v0.1.0 through v0.2.x. The liquidity multipliers really are zero: LDK now relies on the
#: historical tracker instead of the live bounds.
LDK_V0_1_PARAMETERS = LdkScoringParameters(
    base_penalty_msat=1024,
    base_penalty_amount_multiplier_msat=131_072,
    liquidity_penalty_multiplier_msat=0,
    liquidity_penalty_amount_multiplier_msat=0,
    historical_liquidity_penalty_multiplier_msat=10_000,
    historical_liquidity_penalty_amount_multiplier_msat=1_250,
    anti_probing_penalty_msat=250,
    probability_model="degree9",
    probability_scale=64 * (1 << 30),
    zero_min_penalty_numerator=78,
    zero_min_penalty_denominator=64,
)


class LdkWeightFunction(WeightFunction):
    """LDK's ``ProbabilisticScorer`` penalty plus the ``max(fee, htlc_min)`` path cost."""

    def __init__(self, parameters: Optional[LdkScoringParameters] = None) -> None:
        self.params = parameters or LdkScoringParameters()
        self.name = f"ldk-{self.params.probability_model}"

    # ── success probability ──────────────────────────────────────────────────────

    def _probability_parts(
        self,
        amount_msat: int,
        min_liquidity_msat: int,
        max_liquidity_msat: int,
        capacity_msat: int,
        min_zero_implies_no_successes: bool,
    ) -> Tuple[int, int]:
        """``success_probability`` as an exact ``(numerator, denominator)`` pair.

        Kept unreduced because LDK's ``+1`` regularisation and its integer truncation are
        both applied to the scaled integers, and the ratio alone loses that.
        """
        params = self.params
        if params.probability_model == "linear":
            # Note the +1: paper eq. (30) omits it.
            return max_liquidity_msat - amount_msat, (max_liquidity_msat - min_liquidity_msat) + 1

        # Everything is normalised into [0, 1] by capacity *before* the power is taken;
        # the capacity factor does not cancel, because of the +1 and the truncation.
        capacity = float(capacity_msat)
        lower = min_liquidity_msat / capacity - 0.5
        upper = max_liquidity_msat / capacity - 0.5
        amount = amount_msat / capacity - 0.5

        if params.probability_model == "degree9":
            # v0.1.0+: PDF 128 * (1/256 + 9 * (x - 0.5)^8); the 128 cancels in the ratio.
            def integral(u: float) -> float:
                return u**9 + u / 256.0

        else:
            # v0.0.117-v0.0.125: PDF proportional to (x - 0.5)^2.
            def integral(u: float) -> float:
                return u**3

        numerator = int((integral(upper) - integral(amount)) * params.probability_scale) + 1
        denominator = int((integral(upper) - integral(lower)) * params.probability_scale) + 1

        if min_zero_implies_no_successes and min_liquidity_msat == 0:
            # "We have never succeeded through here" discounts the estimate.
            denominator = denominator * params.zero_min_penalty_numerator // params.zero_min_penalty_denominator
        return numerator, denominator

    def success_probability(
        self,
        amount_msat: int,
        min_liquidity_msat: int,
        max_liquidity_msat: int,
        capacity_msat: int,
        min_zero_implies_no_successes: bool = False,
    ) -> float:
        numerator, denominator = self._probability_parts(
            amount_msat, min_liquidity_msat, max_liquidity_msat, capacity_msat, min_zero_implies_no_successes
        )
        if denominator <= 0:
            return 0.0
        return min(1.0, max(0.0, numerator / denominator))

    # ── penalties ────────────────────────────────────────────────────────────────

    @staticmethod
    def _combined_penalty_msat(probability: float, multiplier: int, amount_multiplier: int, amount_msat: int) -> int:
        """``combined_penalty_msat``: ``-log10(P)`` scaled by a flat and an amount term.

        LDK computes ``-log10`` through a 2048-scaled lookup table, so a float ``log10``
        here can differ by a few msat. The saturation at
        :data:`NEGATIVE_LOG10_UPPER_BOUND` and the two independent truncations are exact.
        """
        if probability <= 0.0:
            negative_log10_times_2048 = NEGATIVE_LOG10_UPPER_BOUND * LOG10_SCALE
        else:
            negative_log10_times_2048 = min(
                int(-math.log10(probability) * LOG10_SCALE),
                NEGATIVE_LOG10_UPPER_BOUND * LOG10_SCALE,
            )
        flat = negative_log10_times_2048 * multiplier // LOG10_SCALE
        proportional = (
            negative_log10_times_2048 * amount_multiplier * amount_msat // LOG10_SCALE
        ) // AMOUNT_PENALTY_DIVISOR
        return flat + proportional

    def _liquidity_penalty_msat(self, amount_msat: int, capacity_msat: int) -> int:
        """Penalty from the *live* liquidity bounds, which cold-start to ``[0, capacity]``."""
        params = self.params
        if params.liquidity_penalty_multiplier_msat == 0 and params.liquidity_penalty_amount_multiplier_msat == 0:
            return 0
        if amount_msat >= capacity_msat:
            return (
                self._combined_penalty_msat(
                    0.0,
                    params.liquidity_penalty_multiplier_msat,
                    params.liquidity_penalty_amount_multiplier_msat,
                    amount_msat,
                )
                + params.considered_impossible_penalty_msat
            )

        numerator, denominator = self._probability_parts(amount_msat, 0, capacity_msat, capacity_msat, False)
        # A failure chance below 1/64 is treated as free rather than nearly free.
        if denominator - numerator < denominator // PRECISION_LOWER_BOUND_DENOMINATOR:
            return 0
        return self._combined_penalty_msat(
            numerator / denominator,
            params.liquidity_penalty_multiplier_msat,
            params.liquidity_penalty_amount_multiplier_msat,
            amount_msat,
        )

    def _historical_penalty_msat(self, amount_msat: int, capacity_msat: int) -> int:
        """Penalty from the historical bucket tracker's *no-data* fallback.

        With fewer than 32x32 recorded datapoints LDK ignores the buckets entirely and
        scores ``success_probability(amt, 0, cap, cap, min_zero_implies_no_successes=True)``.
        A gossip snapshot has no datapoints at all, so that is always the branch taken.
        """
        params = self.params
        if (
            params.historical_liquidity_penalty_multiplier_msat == 0
            and params.historical_liquidity_penalty_amount_multiplier_msat == 0
        ):
            return 0
        probability = self.success_probability(amount_msat, 0, capacity_msat, capacity_msat, True)
        return self._combined_penalty_msat(
            probability,
            params.historical_liquidity_penalty_multiplier_msat,
            params.historical_liquidity_penalty_amount_multiplier_msat,
            amount_msat,
        )

    # ── WeightFunction ───────────────────────────────────────────────────────────

    def edge_weight(self, ctx: EdgeContext) -> float:
        """The channel's total penalty. Fees are *not* here — they ride the separate
        accumulator that :meth:`path_cost` folds in with a ``max``."""
        # channel_penalty_msat returns 0 for any candidate that is not a PublicHop, which
        # includes the sender's own outgoing channel.
        if ctx.is_sender_hop:
            return 0.0

        params = self.params
        penalty = (
            params.base_penalty_msat
            + (params.base_penalty_amount_multiplier_msat * ctx.amount_msat) // BASE_AMOUNT_PENALTY_DIVISOR
        )

        capacity = ctx.capacity_msat
        if capacity:
            # Anti-probing applies only when the funding amount is known, and the halving
            # is integer division.
            if ctx.htlc_maximum_msat is not None and ctx.htlc_maximum_msat >= capacity // 2:
                penalty += params.anti_probing_penalty_msat
            penalty += self._liquidity_penalty_msat(ctx.amount_msat, capacity)
            penalty += self._historical_penalty_msat(ctx.amount_msat, capacity)
        return float(penalty)

    def edge_probability(self, ctx: EdgeContext) -> float:
        """Reported for comparability. LDK prices probability but never prunes on it."""
        capacity = ctx.capacity_msat
        if not capacity:
            return 1.0
        return self.success_probability(ctx.amount_msat, 0, capacity, capacity, False)

    def path_htlc_minimum_msat(self, ctx: EdgeContext, downstream_msat: int) -> int:
        """``m = max(downstream, htlc_min_e)``, then this channel's fees on top of ``m``."""
        minimum = max(downstream_msat, ctx.htlc_minimum_msat)
        return minimum + ctx.fee_base_msat + (minimum * ctx.fee_ppm) // 1_000_000

    def path_cost(
        self,
        additive_msat: float,
        fee_msat: int,
        path_htlc_minimum_msat: int,
        probability: float,
        attempt_cost_msat: float,
    ) -> float:
        """``max(total_fee, path_htlc_minimum) + total_penalty`` — LDK's actual heap key.

        The ``max`` is why a channel with a high ``htlc_minimum_msat`` can lose to a
        pricier one: LDK charges the path what it *would* cost to send the smallest HTLC
        the route permits, whichever is larger.
        """
        return float(max(fee_msat, path_htlc_minimum_msat)) + additive_msat

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, **asdict(self.params)}
