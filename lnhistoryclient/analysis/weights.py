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
from typing import Dict, Optional

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
