"""The seam between a snapshot graph and a node implementation's pathfinding logic.

Every LN client solves the same problem — find a cheap, reliable path — but each one
disagrees about what "cheap" and "reliable" mean. Saraswathi & Kümmerle (arXiv:2410.13784)
show that the disagreement is almost entirely captured by two things:

1. an **edge weight function** ``weight: E -> R>=0``, and
2. a set of **side constraints** on the resulting path (Table I of the paper).

This module defines those two things as data, so that :mod:`...analysis.routing` can run
one search engine over any client. Adding a client, or a new version of one, means
writing a :class:`WeightFunction` and a :class:`PathConstraints` — never a new router.

The additive/multiplicative split
---------------------------------
Most clients define a purely *additive* path cost, ``c(p) = sum weight(e)``, which plain
Dijkstra minimises correctly. LND does not: it adds a virtual attempt cost divided by the
*path* success probability, giving

    c(p) = sum_e weight_a(e)  +  c_attempt / prod_e P_e            (paper eq. 6)

which is additive-plus-multiplicative and, as the paper proves by counterexample (Fig. 1),
is **not** guaranteed optimal under LND's modified Dijkstra. We reproduce LND's actual
greedy behaviour rather than the true optimum, because reproducing the client is the whole
point. :attr:`WeightFunction.attempt_cost_msat` returning ``0`` collapses the general form
back to plain additive, so one engine covers both.

Why probability is tracked even for additive clients
----------------------------------------------------
CLN, LDK and eclair fold reliability into the additive term (as ``-log P_e`` or
``FailureCost/P_e``), so they need no multiplicative accumulator. But LDK *also* imposes a
hard ``prod P_e >= 0.01`` side constraint, so the router accumulates the probability
product regardless and consults :attr:`PathConstraints.min_path_probability` to decide
whether it binds.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, NamedTuple, Optional, Tuple

# Blocks mined per year, as used by CLN's risk term. This is CLN's own constant
# (52596 = 6 * 24 * 365.25), not the more common 52560.
BLOCKS_PER_YEAR = 52596


class Implementation(str, Enum):
    """The four node implementations covered by the paper."""

    LND = "lnd"
    CLN = "cln"
    LDK = "ldk"
    ECLAIR = "eclair"


class Algorithm(str, Enum):
    """Which search a client runs. See Section V of the paper.

    ``DIJKSTRA`` is Algorithm 1 (plain, optionally constraint-adhering). ``MODIFIED_DIJKSTRA``
    is Algorithm 2, whose priority queue is keyed on additive-plus-multiplicative cost.
    ``YEN`` layers Yen's K-shortest-paths over Dijkstra, as eclair does.
    """

    DIJKSTRA = "dijkstra"
    MODIFIED_DIJKSTRA = "modified_dijkstra"
    YEN = "yen"


class EdgeContext(NamedTuple):
    """Everything a weight function may look at for one directed channel.

    A ``NamedTuple`` rather than a dataclass because one is constructed per edge
    relaxation — hundreds of thousands per payment on a real snapshot — so construction
    cost is on the hot path.

    ``amount_msat`` is the amount that actually crosses *this* channel, which the reverse
    search knows exactly (see the :mod:`...analysis.routing` module docstring). It already
    includes every downstream forwarding fee.

    ``capacity_msat`` is the *effective* capacity: the enriched on-chain value when
    available, else the ``htlc_maximum_msat`` proxy, else ``None``. Weight functions must
    handle ``None`` — on an un-enriched snapshot a good share of channels have neither.
    """

    #: Amount crossing this channel, i.e. what the downstream endpoint receives.
    amount_msat: int
    #: Fee this edge's source charges to forward ``amount_msat``. Zero on the sender's
    #: own channel, which originates the payment rather than forwarding it.
    fee_msat: int
    fee_base_msat: int
    fee_ppm: int
    cltv_expiry_delta: int
    htlc_minimum_msat: int
    htlc_maximum_msat: Optional[int]
    capacity_msat: Optional[int]
    #: Bitcoin block that funded the channel, decoded from the scid. ``0`` if unknown.
    funding_block: int
    #: True on the sender's own channel; several clients skip fee-derived penalties there.
    is_sender_hop: bool


@dataclass(frozen=True)
class NetworkStats:
    """Graph-level aggregates a weight function may need to normalise per-edge values.

    Computed once per search by the router and handed to :meth:`WeightFunction.prepare`.
    Only eclair currently uses this (channel age is relative to the chain tip), but any
    weight function that normalises against the network rather than against constants
    needs it.
    """

    #: Highest funding block seen in the snapshot — a lower bound on the chain tip.
    tip_block_height: int
    #: Lowest funding block seen, i.e. the oldest channel still open.
    min_funding_block: int


class WeightFunction(ABC):
    """A client's per-edge cost. Lower is preferred; must be non-negative.

    Subclasses implement :meth:`edge_weight`. Override :meth:`edge_probability` when the
    client models channel success probability, and :meth:`attempt_cost_msat` only for the
    additive-plus-multiplicative form (LND).
    """

    #: Human-readable identifier, e.g. ``"lnd-apriori"``. Used in comparison output.
    name: str = "weight"

    def prepare(self, stats: NetworkStats, amount_msat: int) -> None:  # noqa: B027
        """Hook called once per payment, before any relaxation.

        Default is a no-op. Override to cache graph-level normalisation ranges (eclair
        needs the chain tip to price channel age) or the payment amount (eclair's failure
        cost and LND's attempt cost are both scalars derived from it).

        An instance is therefore **bound to one search at a time** — share a
        :class:`ClientProfile` across threads only by cloning it.
        """

    @abstractmethod
    def edge_weight(self, ctx: EdgeContext) -> float:
        """Cost of traversing this directed channel with ``ctx.amount_msat``.

        Summed over the path into the *additive* accumulator. For clients whose total cost
        is more than a sum (LDK), this is only the additive part and :meth:`path_cost`
        does the rest.
        """

    def edge_probability(self, ctx: EdgeContext) -> float:
        """Estimated probability this channel can forward ``ctx.amount_msat``.

        Returns ``1.0`` by default, which makes the multiplicative accumulator inert and
        any ``min_path_probability`` constraint vacuous.
        """
        return 1.0

    def attempt_cost_msat(self, amount_msat: int) -> float:
        """Virtual cost of one failed payment attempt, divided by the path probability.

        Non-zero only for LND. Computed once per payment from the *total* amount, matching
        LND, where ``absoluteAttemptCost`` is a scalar for the whole search rather than a
        per-edge term. Note this differs from the paper's eq. (13), which presents the term
        per-edge and would therefore multiply it by the hop count when summed; the paper's
        own eq. (6) and LND's source agree with the scalar reading used here.
        """
        return 0.0

    def path_htlc_minimum_msat(self, ctx: EdgeContext, downstream_msat: int) -> int:
        """LDK's running "what would the smallest allowed HTLC cost here" accumulator.

        Defined by the recurrence in LDK's ``add_entry!``::

            m = max(downstream_msat, htlc_minimum_msat_e)
            path_htlc_minimum = m + base_fee_e + m * ppm_e / 1e6

        Returns ``0`` for every other client, which makes it inert in :meth:`path_cost`.
        Note the paper's eq. (23) omits the ``max`` against the downstream accumulator, so
        it is only right for the final hop.
        """
        return 0

    def path_cost(
        self,
        additive_msat: float,
        fee_msat: int,
        path_htlc_minimum_msat: int,
        probability: float,
        attempt_cost_msat: float,
    ) -> float:
        """Fold the path accumulators into the value the client's priority queue orders on.

        The default is the additive-plus-multiplicative form of paper eq. (6), which
        degenerates to a plain sum when :meth:`attempt_cost_msat` is zero — covering LND,
        CLN and eclair. LDK overrides it, because it takes ``max(fee, path_htlc_minimum)``
        rather than adding the fee in.
        """
        if attempt_cost_msat == 0.0:
            return additive_msat
        return additive_msat + attempt_cost_msat / probability

    def describe(self) -> Dict[str, Any]:
        """Parameters in force, for provenance in study output."""
        return {"name": self.name}

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self.name!r})"


@dataclass(frozen=True)
class FeeBudget:
    """A client's maximum acceptable total routing fee. The three clients that set one
    all combine a flat and a proportional part, but not in the same way:

    * LDK — ``50 sat + 1%`` of the amount (**sum**).
    * eclair — ``max(21 sat, 3%)`` of the amount (**max**, so small payments get the flat
      allowance and large ones the percentage).
    * LND — ``5%``, except that anything at or below 1000 sat may spend up to **100%** on
      fees, which is what ``full_amount_below_msat`` expresses.
    """

    base_msat: int = 0
    ppm: int = 0
    #: ``"sum"`` adds the two parts (LDK); ``"max"`` takes the larger (eclair).
    combine: str = "sum"
    #: At or below this amount the entire payment may be spent on fees (LND).
    full_amount_below_msat: int = 0

    def limit_msat(self, amount_msat: int) -> int:
        if self.full_amount_below_msat and amount_msat <= self.full_amount_below_msat:
            return amount_msat
        proportional = (amount_msat * self.ppm) // 1_000_000
        if self.combine == "max":
            return max(self.base_msat, proportional)
        return self.base_msat + proportional


@dataclass(frozen=True)
class PathConstraints:
    """Side constraints of paper eq. (12), i.e. Table I.

    ``enforce_during_search`` distinguishes the two ways clients apply these. Enforcing
    *during* the search (LND, LDK) is the colored modification of Algorithm 1: a
    relaxation that would breach the bound is skipped. That turns pathfinding into the
    NP-complete constrained-shortest-path problem, so a greedy Dijkstra may now miss a
    feasible path — a real behaviour of these clients, not a bug here. Validating *after*
    (CLN, eclair — starred in Table I) instead rejects the finished path, which can return
    no route even when a compliant one exists.
    """

    max_cltv_expiry_delta: Optional[int] = None
    min_path_probability: Optional[float] = None
    max_path_length: Optional[int] = None
    fee_budget: Optional[FeeBudget] = None
    enforce_during_search: bool = True

    def fee_limit_msat(self, amount_msat: int) -> Optional[int]:
        return None if self.fee_budget is None else self.fee_budget.limit_msat(amount_msat)

    def describe(self) -> Dict[str, Any]:
        return {
            "max_cltv_expiry_delta": self.max_cltv_expiry_delta,
            "min_path_probability": self.min_path_probability,
            "max_path_length": self.max_path_length,
            "fee_budget": (
                None
                if self.fee_budget is None
                else {
                    "base_msat": self.fee_budget.base_msat,
                    "ppm": self.fee_budget.ppm,
                }
            ),
            "enforce_during_search": self.enforce_during_search,
        }


@dataclass(frozen=True)
class ClientProfile:
    """A specific client, version and configuration variant, ready to route with.

    Build these via :func:`...clients.registry.client_profile` rather than directly, so
    that a version string resolves to the behaviour that version actually shipped.
    """

    implementation: Implementation
    version: str
    variant: str
    weight: WeightFunction
    constraints: PathConstraints
    algorithm: Algorithm = Algorithm.DIJKSTRA
    #: Candidate paths for :attr:`Algorithm.YEN`; ignored otherwise.
    k_paths: int = 1
    #: Looser bounds to retry with when the first pass finds nothing. eclair really does
    #: this — it searches within a 6-hop / configured-cltv envelope first and falls back to
    #: its hard ceilings — and skipping the retry would understate its success rate.
    relaxed_constraints: Optional[PathConstraints] = None
    #: Caveats about fidelity to the real implementation at this version.
    notes: str = ""
    #: Provenance for the constants used — upstream file/symbol, or the paper's equation.
    sources: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """Compact identifier such as ``lnd-bimodal@0.17.4``."""
        base = self.implementation.value
        if self.variant and self.variant != "default":
            base = f"{base}-{self.variant}"
        return f"{base}@{self.version}"

    def describe(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "implementation": self.implementation.value,
            "version": self.version,
            "variant": self.variant,
            "algorithm": self.algorithm.value,
            "k_paths": self.k_paths,
            "weight": self.weight.describe(),
            "constraints": self.constraints.describe(),
            "notes": self.notes,
            "sources": list(self.sources),
        }

    def __repr__(self) -> str:
        return f"ClientProfile({self.label}, {self.algorithm.value})"
