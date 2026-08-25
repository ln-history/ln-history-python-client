"""Edge-weight conventions — the single source of truth for every analytic.

The word "weight" means different things depending on whether a metric walks shortest
paths or sums adjacent edges, and depending on whether we weight by fee or capacity.
Centralising the conventions here keeps the sign/inversion logic in exactly one place:

* **Path metrics** (betweenness, closeness, routing) need a *distance* — lower means
  "closer" / preferred. See :func:`path_distance`.

  - ``FEE``      → distance = fee to forward ``amount_sat`` (a cost; higher = worse).
  - ``CAPACITY`` → distance = ``1 / capacity`` (a high-capacity edge is *preferred*,
    so capacity must be inverted to behave as a distance).
  - ``NONE``     → distance = 1 (hop count).

* **Strength metrics** (weighted degree, weighted PageRank) need a *magnitude* — higher
  means "more". See :func:`edge_strength`.

  - ``CAPACITY`` → raw capacity in sat.
  - ``NONE``     → 1.
  - ``FEE``      → not meaningful; rejected.

Fees are amount-dependent (``fee_base_msat + amount * ppm / 1e6``), so path/strength
resolution takes an ``amount_sat`` with a fixed reference default.
"""

from enum import Enum
from typing import Dict, Optional, Sequence

from lnhistoryclient.graph.projections import edge_capacity_sat

# Reference payment used whenever a fee-based weight needs an amount. 100k sat is a
# typical mid-size Lightning payment.
DEFAULT_AMOUNT_SAT = 100_000

# Distance assigned to a capacity-weighted edge whose capacity is unknown (no update,
# no enrichment). Large enough that routing/pathing avoids it, finite so algorithms
# that dislike ``inf`` still work. Occurrences are counted and logged by callers.
MISSING_CAPACITY_DISTANCE = 1e12


class Weighting(str, Enum):
    """How to weight edges. Accepts the bare strings ``"none"``/``"fee"``/``"capacity"``."""

    NONE = "none"
    FEE = "fee"
    CAPACITY = "capacity"


def coerce_weighting(weight: Optional[object]) -> Weighting:
    """Normalise ``None`` / ``str`` / :class:`Weighting` into a :class:`Weighting`."""
    if weight is None:
        return Weighting.NONE
    if isinstance(weight, Weighting):
        return weight
    return Weighting(str(weight).lower())


def fee_msat(attrs: Dict[str, object], amount_sat: int) -> int:
    """Fee in millisatoshis to forward ``amount_sat`` over an edge with this policy."""
    base = attrs.get("fee_base_msat")
    ppm = attrs.get("fee_proportional_millionths")
    base_int = base if isinstance(base, int) else 0
    ppm_int = ppm if isinstance(ppm, int) else 0
    amount_msat = amount_sat * 1000
    return base_int + (amount_msat * ppm_int) // 1_000_000


#: lnd clamps the advertised inbound rate at fee-calculation time (models.maxFeeRate).
MAX_INBOUND_FEE_RATE_PPM = 10_000_000


def inbound_fee_msat(attrs: Dict[str, object], amount_sat: int) -> int:
    """Inbound fee the *destination* of this edge charges to receive ``amount_sat``.

    Reads the ``dst_inbound_fee_*`` attributes that
    :func:`~lnhistoryclient.graph.builder.build_multidigraph` populates when called with
    ``inbound_fees=True``; returns 0 when they are absent, so an un-opted-in graph prices
    exactly as it did before.

    The result is **signed**: negative is the normal case, a discount.
    """
    base = attrs.get("dst_inbound_fee_base_msat")
    ppm = attrs.get("dst_inbound_fee_proportional_millionths")
    base_int = base if isinstance(base, int) else 0
    ppm_int = ppm if isinstance(ppm, int) else 0
    if ppm_int > MAX_INBOUND_FEE_RATE_PPM:
        ppm_int = MAX_INBOUND_FEE_RATE_PPM
    elif ppm_int < -MAX_INBOUND_FEE_RATE_PPM:
        ppm_int = -MAX_INBOUND_FEE_RATE_PPM
    return base_int + (amount_sat * 1000 * ppm_int) // 1_000_000


def hop_fee_msat(
    incoming: Optional[Dict[str, object]],
    outgoing: Dict[str, object],
    amount_sat: int,
) -> int:
    """Total fee a forwarding node charges, following lnd's rule.

    A routing node's charge is **not** a property of one edge. It is the outbound fee of
    the channel it forwards out on *plus* the inbound fee it advertises on the channel the
    HTLC arrived on, and lnd clamps the **sum** rather than either part::

        signedFee := inboundFee + outboundFee
        fee := 0; if signedFee > 0 { fee = signedFee }

    (``routing/pathfind.go``. Dijkstra cannot carry negative edge weights, which is why
    the clamp exists at all.) A discount can therefore cancel an outbound fee entirely but
    can never make a hop pay the sender.

    Args:
        incoming: Attributes of the edge the payment arrives on, or ``None`` for the
            first hop -- the sender charges nothing and no inbound fee applies.
        outgoing: Attributes of the edge the payment leaves on.
        amount_sat: Amount being forwarded.

    Because the fee depends on a *pair* of edges, it cannot be folded into a single
    per-edge weight, and a shortest-path search that prices inbound fees correctly has to
    carry the incoming channel in its search state. That is exactly what lnd does, and it
    is why this function takes two edges rather than one.
    """
    outbound = fee_msat(outgoing, amount_sat)
    if incoming is None:
        return outbound
    signed = inbound_fee_msat(incoming, amount_sat) + outbound
    return signed if signed > 0 else 0


def route_fee_msat(
    edges: Sequence[Dict[str, object]],
    amount_sat: int,
    inbound: bool = True,
) -> int:
    """Total fee paid across a route, given its edges in traversal order.

    The last edge is the delivery to the recipient, who charges nothing, so fees accrue
    at every intermediate node: for edge *i* (0-indexed) the forwarding node is the one it
    leaves from, and the channel it arrived on is edge *i-1*.

    Set ``inbound=False`` to price the same route ignoring inbound fees, which is what
    every pre-TLV model does -- the difference between the two is the mispricing.
    """
    total = 0
    for index in range(1, len(edges)):
        incoming = edges[index - 1] if inbound else None
        total += hop_fee_msat(incoming, edges[index], amount_sat)
    return total


def path_distance(
    attrs: Dict[str, object],
    weighting: Weighting,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
) -> float:
    """Distance for a directed edge under a path-based metric (lower = preferred).

    ``CAPACITY`` inverts capacity; unknown capacity yields
    :data:`MISSING_CAPACITY_DISTANCE`. ``FEE`` returns the forwarding fee for
    ``amount_sat``. ``NONE`` returns ``1.0`` (hop count).
    """
    if weighting is Weighting.NONE:
        return 1.0
    if weighting is Weighting.FEE:
        return float(fee_msat(attrs, amount_sat))
    # CAPACITY
    capacity = edge_capacity_sat(attrs)
    if not capacity or capacity <= 0:
        return MISSING_CAPACITY_DISTANCE
    return 1.0 / capacity


def edge_strength(
    attrs: Dict[str, object],
    weighting: Weighting,
    amount_sat: int = DEFAULT_AMOUNT_SAT,
) -> float:
    """Magnitude for a strength-based metric (higher = more).

    ``CAPACITY`` returns raw capacity in sat (0 when unknown); ``NONE`` returns 1.
    ``FEE`` is not a meaningful strength and raises ``ValueError``.
    """
    if weighting is Weighting.NONE:
        return 1.0
    if weighting is Weighting.CAPACITY:
        capacity = edge_capacity_sat(attrs)
        return float(capacity) if capacity else 0.0
    raise ValueError("FEE weighting is not meaningful for strength-based metrics")
