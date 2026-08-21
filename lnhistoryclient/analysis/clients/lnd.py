"""LND's pathfinding cost — paper Section VI-A, verified against ``routing/pathfind.go``.

LND is the only client whose path cost is not a plain sum. It keys its priority queue on

    dist(v) = W(v -> target) + absoluteAttemptCost / P(v -> target)

where ``W`` is the accumulated additive weight and ``P`` the accumulated *product* of
per-channel success probabilities (``pathfind.go:730``, ``getProbabilityBasedDist``). The
attempt cost enters **once, globally** — not once per hop. Paper eq. (13) presents it
per-edge, which when summed would scale it by the hop count; eq. (6) and the source agree
with the global reading implemented here.

Corrections to the paper
------------------------
Three of the paper's LND constants do not match the source at v0.17.4-beta:

* ``DefaultAttemptCost`` is ``lnwire.NewMSatFromSatoshis(100)`` — **100 sat = 100 000
  msat**, not 100 msat. A 1000x error, and the single most consequential one: it sets the
  whole fee-versus-reliability trade-off.
* The apriori capacity factor is a **logistic**. The source computes
  ``1 - 0.5 / (1 + exp((c_o*cap - amt) / (s_o*cap)))``; the paper's eq. (17) drops the
  ``exp``, giving a function with a pole rather than a sigmoid.
* ``minCapacityFactor = 0.5`` is an asymptote that is never attained for ``amt <= cap``:
  the realised range is ``[1.0, 0.75]``, dropping hard to ``0`` once ``amt > cap``.

What is deliberately not modelled
---------------------------------
Mission Control. Both estimators here return their **cold-start** value, because a
snapshot carries no payment history. In a running node the apriori estimator would return
a flat ``0.95`` for amounts below a known-good one and decay a failure back over a
one-hour half-life, and the bimodal estimator would narrow its bounds; neither has an
analogue in gossip data. The paper's simulation makes the same simplification.
"""

import math
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from lnhistoryclient.analysis.clients.base import EdgeContext, WeightFunction

# routing/pathfind.go:34 — billionths of msat per msat per block of timelock delta.
RISK_FACTOR_BILLIONTHS = 15
# routing/pathfind.go:58-73. NewMSatFromSatoshis(100), i.e. 100 sat.
DEFAULT_ATTEMPT_COST_MSAT = 100_000
DEFAULT_ATTEMPT_COST_PPM = 1000
# routing/pathfind.go:77
DEFAULT_MIN_ROUTE_PROBABILITY = 0.01

# routing/pathfind.go:81 and routing/probability_apriori.go:26-45
DEFAULT_APRIORI_HOP_PROBABILITY = 0.6
DEFAULT_CAPACITY_FRACTION = 0.9999
CAPACITY_SMEARING_FRACTION = 0.025
MIN_CAPACITY_FACTOR = 0.5

# routing/probability_bimodal.go:14 — 3e8 msat = 300 000 sat. The constant, the config
# field and the CLI flag are all in *msat*; the paper quotes it in sat.
DEFAULT_BIMODAL_SCALE_MSAT = 300_000_000


class LndProbabilityEstimator(ABC):
    """LND's ``ProbabilitySource``: how likely is this channel to forward this amount?"""

    name: str = "estimator"

    @abstractmethod
    def probability(self, amount_msat: int, capacity_msat: Optional[int]) -> float:
        """Cold-start estimate in ``[0, 1]``."""

    def describe(self) -> Dict[str, Any]:
        return {"estimator": self.name}


class AprioriEstimator(LndProbabilityEstimator):
    """LND's default estimator: a flat prior scaled by a capacity-based logistic.

    ``routing/probability_apriori.go:268-303``. With no payment history the node
    probability *is* ``AprioriHopProbability * capacityFactor``, so this is exactly what a
    freshly started node would use.
    """

    name = "apriori"

    def __init__(
        self,
        hop_probability: float = DEFAULT_APRIORI_HOP_PROBABILITY,
        capacity_fraction: float = DEFAULT_CAPACITY_FRACTION,
        smearing_fraction: float = CAPACITY_SMEARING_FRACTION,
        min_capacity_factor: float = MIN_CAPACITY_FACTOR,
    ) -> None:
        self.hop_probability = hop_probability
        self.capacity_fraction = capacity_fraction
        self.smearing_fraction = smearing_fraction
        self.min_capacity_factor = min_capacity_factor

    def capacity_factor(self, amount_msat: int, capacity_msat: Optional[int]) -> float:
        """The logistic of ``probability_apriori.go:268``; ``1.0`` when disabled/unknown."""
        # Both guards are in the source: an unknown capacity (hop hints) and a disabled
        # factor must not penalise the channel at all.
        if self.capacity_fraction >= 1.0 or not capacity_msat:
            return 1.0
        if amount_msat > capacity_msat:
            return 0.0
        cutoff = self.capacity_fraction * capacity_msat
        smearing = self.smearing_fraction * capacity_msat
        denominator = 1.0 + math.exp(-(amount_msat - cutoff) / smearing)
        return 1.0 - (1.0 - self.min_capacity_factor) / denominator

    def probability(self, amount_msat: int, capacity_msat: Optional[int]) -> float:
        return self.hop_probability * self.capacity_factor(amount_msat, capacity_msat)

    def describe(self) -> Dict[str, Any]:
        return {
            "estimator": self.name,
            "hop_probability": self.hop_probability,
            "capacity_fraction": self.capacity_fraction,
            "smearing_fraction": self.smearing_fraction,
        }


class BimodalEstimator(LndProbabilityEstimator):
    """LND's opt-in bimodal estimator (``--routerrpc.estimator=bimodal``, v0.16.0+).

    Models liquidity as concentrated at the two ends of the channel,
    ``P(x) ~ e^(-x/s) + e^((x-c)/s)``, and integrates it. With no payment history the
    known-good amount is ``0`` and the known-bad amount is the capacity, so this reduces
    to the unconditional prior of paper eq. (18).
    """

    name = "bimodal"

    def __init__(self, scale_msat: int = DEFAULT_BIMODAL_SCALE_MSAT) -> None:
        if scale_msat <= 0:
            raise ValueError(f"bimodal scale must be positive, got {scale_msat}")
        self.scale_msat = scale_msat

    def _primitive(self, capacity_msat: float, x: float) -> float:
        """Antiderivative of the density, per ``probability_bimodal.go:426``."""
        scale = float(self.scale_msat)
        norm = 2.0 - 2.0 * math.exp(-capacity_msat / scale)
        if norm <= 0.0:
            # capacity <<< scale: the density is flat and the normaliser degenerates.
            return 0.0
        return (-math.exp(-x / scale) + math.exp((x - capacity_msat) / scale)) / norm

    def probability(self, amount_msat: int, capacity_msat: Optional[int]) -> float:
        if not capacity_msat:
            return 1.0
        if amount_msat > capacity_msat:
            return 0.0
        capacity = float(capacity_msat)
        # Cold start: nothing is known to succeed, nothing is known to fail below capacity.
        denominator = self._primitive(capacity, capacity) - self._primitive(capacity, 0.0)
        if denominator <= 0.0:
            return 0.0
        numerator = self._primitive(capacity, capacity) - self._primitive(capacity, float(amount_msat))
        return min(1.0, max(0.0, numerator / denominator))

    def describe(self) -> Dict[str, Any]:
        return {"estimator": self.name, "scale_msat": self.scale_msat}


class UniformEstimator(LndProbabilityEstimator):
    """``P = (cap - amt) / cap`` — the paper's own proposal, its "LND-un" variant.

    Not an LND setting: eq. (43) is the authors' modification, which their experiments
    found gave the highest success rates of any variant tested. Kept here so that result
    is reproducible.
    """

    name = "uniform"

    def probability(self, amount_msat: int, capacity_msat: Optional[int]) -> float:
        if not capacity_msat:
            return 1.0
        if amount_msat >= capacity_msat:
            return 0.0
        return (capacity_msat - amount_msat) / capacity_msat


class LndWeightFunction(WeightFunction):
    """``fee + amount * cltv_delta * riskfactor``, plus a global attempt-cost term.

    ``edgeWeight`` (``pathfind.go:275``) is integer arithmetic throughout, and the
    truncation is not cosmetic: ``amt_msat * delta * 15`` is divided by ``1e9`` *after*
    multiplying, so the timelock penalty is exactly zero until ``amt * delta`` exceeds
    ~6.7e7. A 1000-sat payment over a 40-block delta pays no timelock penalty at all.
    """

    def __init__(
        self,
        estimator: Optional[LndProbabilityEstimator] = None,
        *,
        risk_factor_billionths: int = RISK_FACTOR_BILLIONTHS,
        attempt_cost_base_msat: int = DEFAULT_ATTEMPT_COST_MSAT,
        attempt_cost_ppm: int = DEFAULT_ATTEMPT_COST_PPM,
        time_preference: float = 0.0,
        locked_amount_includes_fee: bool = True,
    ) -> None:
        if not -1.0 <= time_preference <= 1.0:
            raise ValueError(f"time preference must be in [-1, 1], got {time_preference}")
        self.estimator = estimator or AprioriEstimator()
        self.risk_factor_billionths = risk_factor_billionths
        self.attempt_cost_base_msat = attempt_cost_base_msat
        self.attempt_cost_ppm = attempt_cost_ppm
        self.time_preference = time_preference
        # v0.17.x weighs the timelock against amountToReceive (amount + this hop's fee);
        # v0.19.0+ switched to amountToSend. See the version table in registry.py.
        self.locked_amount_includes_fee = locked_amount_includes_fee
        self.name = f"lnd-{self.estimator.name}"

    def edge_weight(self, ctx: EdgeContext) -> float:
        locked = ctx.amount_msat + ctx.fee_msat if self.locked_amount_includes_fee else ctx.amount_msat
        timelock_penalty = (locked * ctx.cltv_expiry_delta * self.risk_factor_billionths) // 1_000_000_000
        return float(ctx.fee_msat + timelock_penalty)

    def edge_probability(self, ctx: EdgeContext) -> float:
        return self.estimator.probability(ctx.amount_msat, ctx.capacity_msat)

    def attempt_cost_msat(self, amount_msat: int) -> float:
        """``pathfind.go:615-635``. At the default ``time_preference = 0`` the multiplier
        is exactly 1; ``-1`` optimises for fees only (x0.0526) and ``+1`` for reliability
        only (x19)."""
        base = self.attempt_cost_base_msat + (amount_msat * self.attempt_cost_ppm) // 1_000_000
        scaled_preference = self.time_preference * 0.9
        return float(base) * (1.0 / (0.5 - scaled_preference / 2.0) - 1.0)

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "risk_factor_billionths": self.risk_factor_billionths,
            "attempt_cost_base_msat": self.attempt_cost_base_msat,
            "attempt_cost_ppm": self.attempt_cost_ppm,
            "time_preference": self.time_preference,
            "locked_amount_includes_fee": self.locked_amount_includes_fee,
            **self.estimator.describe(),
        }
