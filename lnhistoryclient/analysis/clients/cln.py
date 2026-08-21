"""Core Lightning's pathfinding cost — paper Section VI-B, verified against the C source.

    weight(e) = (fee + risk + 1) * (capacity_bias + 1)

``plugins/libplugin-pay.c::route_score``, with ``risk`` from ``common/dijkstra.c::risk_price``
and ``capacity_bias`` from the function of that name. The paper's eq. (20) and (21) are
**exactly right**, including both ``+1`` terms — one is a deliberate 1-msat-per-hop
tiebreaker that biases toward shorter paths, the other keeps the multiplicative bias from
zeroing the whole cost.

Two things the paper gets wrong or leaves out
---------------------------------------------
* **The hop limit is 20, not 10.** ``ROUTING_MAX_HOPS`` is 20
  (``common/gossip_constants.h:8``). The 10 in the paper is ``ROUTING_MAX_HOPS / 2``,
  which trims *invoice routehints*, not payment paths.
* **``getroute`` and ``pay`` do not score alike.** Only ``pay`` uses the formula above.
  The ``getroute`` RPC uses ``route_score_cheaper``, which has **no capacity bias at all**
  — see the ``getroute`` variant below. Anyone comparing this library against a node's
  ``getroute`` output needs that variant, not the default.

Capacity is a *bias*, not a limit
---------------------------------
Unlike eclair, CLN does not reject a channel for being too small: capacity enters only
through the logarithmic bias term, so an over-capacity hop stays reachable at a steep
price. Our router still applies :attr:`~...routing.Constraint.CAPACITY` as a hard filter
when a real on-chain capacity is known, which is the stricter (and more useful) reading;
pass a relaxed ``Constraint`` to reproduce CLN's own tolerance.

Version stability
-----------------
This formula is **unchanged from v0.10 through v26.04** — verified byte-identical in
``libplugin-pay.c`` across the range. The ``askrene`` min-cost-flow engine shipped in
v24.08 but only took over ``pay`` in **v26.06**; ``renepay`` (v23.08) was always opt-in.
So a single profile covers essentially the whole history of the network, and nothing here
models askrene.
"""

import math
from typing import Any, Dict

from lnhistoryclient.analysis.clients.base import EdgeContext, WeightFunction

#: ``common/dijkstra.c:107`` — 365.25 * 24 * 60 / 10. Note eclair uses 52560 instead;
#: do not share a constant between the two.
BLOCKS_PER_YEAR = 52596
#: ``plugins/pay.c`` — riskfactor is a *percentage per year*, default 10.
DEFAULT_RISK_FACTOR_PERCENT = 10.0
#: ``common/gossip_constants.h:8``
ROUTING_MAX_HOPS = 20
#: ``route_score`` clamps each edge's score into a u32.
MAX_EDGE_SCORE = 0xFFFFFFFF


class ClnWeightFunction(WeightFunction):
    """CLN's ``route_score``: additive fee-and-risk cost, scaled by a capacity bias."""

    def __init__(
        self,
        *,
        risk_factor_percent: float = DEFAULT_RISK_FACTOR_PERCENT,
        blocks_per_year: int = BLOCKS_PER_YEAR,
        use_capacity_bias: bool = True,
    ) -> None:
        self.risk_factor_percent = risk_factor_percent
        self.blocks_per_year = blocks_per_year
        #: ``False`` reproduces ``route_score_cheaper``, which the ``getroute`` RPC uses.
        self.use_capacity_bias = use_capacity_bias
        self.name = "cln" if use_capacity_bias else "cln-getroute"

    def capacity_bias(self, ctx: EdgeContext) -> float:
        """``-ln((cap + 1 - amt) / (cap + 1))``, in msat. Zero when capacity is unknown.

        Grows without bound as the amount approaches the capacity, which is how CLN
        expresses "this channel is probably too full" without hard-rejecting it.
        """
        capacity = ctx.capacity_msat
        if not capacity:
            # gossmap could not resolve a funding amount; the C code returns 0 here too.
            return 0.0
        remaining = capacity + 1 - ctx.amount_msat
        if remaining <= 0:
            # The amount exceeds the channel: C would take log of a non-positive number.
            # Saturate instead, which lands on the same clamped score.
            return float("inf")
        return -math.log(remaining / (capacity + 1))

    def edge_weight(self, ctx: EdgeContext) -> float:
        # risk_price(): amount scaled by riskfactor%/year prorated over the delta's blocks.
        risk_msat = int(
            ctx.amount_msat * (self.risk_factor_percent / 100.0 / self.blocks_per_year * ctx.cltv_expiry_delta)
        )
        cost = ctx.fee_msat + risk_msat + 1

        if not self.use_capacity_bias:
            return float(min(cost, MAX_EDGE_SCORE))

        bias = self.capacity_bias(ctx)
        if math.isinf(bias):
            return float(MAX_EDGE_SCORE)
        return float(min(cost * (bias + 1.0), MAX_EDGE_SCORE))

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "risk_factor_percent": self.risk_factor_percent,
            "blocks_per_year": self.blocks_per_year,
            "use_capacity_bias": self.use_capacity_bias,
        }
