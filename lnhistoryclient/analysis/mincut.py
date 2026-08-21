"""Upper bound on payment feasibility via sampled max-flows / min-cuts.

This reproduces the method of Rene Pickhardt's study `An upper Bound for the Probability
to be able to successfully conduct a Payment on the Lightning Network
<https://github.com/renepickhardt/Lightning-Network-Limitations>`_ (independently
verified at https://stacker.news/items/412708).

The question
------------
Gossip tells us a channel's *capacity* but never its *balance* — the private split
between the two ends. So for a given snapshot the network is not one graph but a huge
family of them, one per liquidity state. A payment of ``a`` sat from ``S`` to ``R`` is
**possible at all** in a given state exactly when the max-flow from ``S`` to ``R`` is at
least ``a`` (max-flow = min-cut). Asking how often that holds, over states and over
payment pairs, bounds how well the network can possibly do.

The estimator
-------------
The state space is combinatorial — ``prod(c_i + 1)`` over channels — so we sample it.
One trial is:

1. draw a fresh liquidity state: for every channel of capacity ``c``, draw
   ``r ~ Uniform{0..c}`` and give one direction ``r``, the other ``c - r``;
2. draw an ordered pair of distinct nodes ``(S, R)`` uniformly;
3. record the max-flow value from ``S`` to ``R``.

``n`` trials give ``n`` samples of the min-cut. :func:`feasibility` then reads off
``P(min-cut >= amount)`` and :func:`amount_at_service_level` inverts it.

Why this is an *upper* bound
----------------------------
The max-flow assumes the sender has **full knowledge of every balance in the network**
and can split the payment arbitrarily. Real senders probe in the dark. So a payment
counted as feasible here may still fail in practice; one counted as infeasible cannot
succeed no matter how clever the sender is, short of moving liquidity on-chain.

What the numbers are conditional on
-----------------------------------
Three assumptions, all of them questionable and all of them Pickhardt's (kept so the
results stay comparable):

* **balances are uniform on ``[0, c]`` and independent across channels** — real
  operators rebalance, and channel states are correlated through the payments that
  created them;
* **every liquidity state is equally likely**;
* **payment pairs are uniform over nodes** — real traffic is nothing like uniform, and
  this is the assumption that most directly moves the headline number.

Parallel channels between the same pair are merged into one channel of summed capacity
before the split is drawn, as in the original.

Backends
--------
``igraph`` is used when importable and is roughly 60x faster than the ``networkx``
fallback, which matters because a sweep is ``trials x snapshots`` max-flows. Both are
exact: igraph carries capacities as doubles, which represent integers below ``2**53``
without loss, and satoshi values stay far below that.

``scipy.sparse.csgraph.maximum_flow`` is deliberately **not** used: it requires
``int32`` capacities and, given ``int64`` input, silently returns ``0`` rather than
raising. Node strengths on a real snapshot exceed ``int32`` (500+ BTC), so it would
quietly produce wrong answers.
"""

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import networkx as nx
import numpy as np

from lnhistoryclient.graph.projections import edge_capacity_sat, to_undirected_simple

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised by whichever backend is installed
    import igraph as _igraph

    HAVE_IGRAPH = True
except ImportError:  # pragma: no cover
    _igraph = None
    HAVE_IGRAPH = False

#: A channel reduced to what this model needs: two endpoints and a capacity in satoshis.
Channel = Tuple[str, str, int]


def channel_table(graph: nx.MultiDiGraph) -> List[Channel]:
    """Reduce a snapshot graph to ``(node_a, node_b, capacity_sat)`` triples.

    Parallel channels are merged into a single capacity (via
    :func:`~lnhistoryclient.graph.projections.to_undirected_simple`), matching the
    original study. Channels whose capacity is unknown are dropped — attach real
    capacities first with :func:`~lnhistoryclient.graph.attach_capacity`, since
    ``channel_announcement`` gossip does not carry them.
    """
    simple = to_undirected_simple(graph)
    table: List[Channel] = []
    dropped = 0
    for node_a, node_b, attrs in simple.edges(data=True):
        capacity = edge_capacity_sat(attrs)
        if capacity is None or capacity <= 0:
            dropped += 1
            continue
        table.append((node_a, node_b, int(capacity)))
    if dropped:
        logger.info("channel_table: dropped %d channels with no known capacity", dropped)
    return table


def induced_subgraph(channels: Sequence[Channel], keep: Iterable[str]) -> List[Channel]:
    """Keep only channels whose **both** endpoints are in ``keep``.

    This is how the "professional" subnetwork is formed: the BOS-scored nodes and the
    channels *between* them. Channels from a BOS node out to the wider network are not
    part of that subnetwork's own liquidity.
    """
    members = set(keep)
    return [(a, b, c) for a, b, c in channels if a in members and b in members]


@dataclass(frozen=True)
class MinCutSamples:
    """Sampled min-cut values for one graph, plus the graph's vital statistics.

    ``values`` holds one max-flow per trial, in satoshis.
    """

    values: np.ndarray
    nodes: int
    channels: int
    total_capacity_sat: int
    trials: int
    seed: int
    label: str = ""
    scope: str = ""

    @property
    def avg_degree(self) -> float:
        """Mean number of channels per node."""
        return (2.0 * self.channels / self.nodes) if self.nodes else 0.0

    @property
    def capacity_per_node_sat(self) -> float:
        """Mean capacity attached to a node (each channel counted for both ends)."""
        return (2.0 * self.total_capacity_sat / self.nodes) if self.nodes else 0.0

    def to_dict(self) -> Dict[str, object]:
        """JSON-serialisable summary plus the raw samples."""
        return {
            "label": self.label,
            "scope": self.scope,
            "trials": self.trials,
            "seed": self.seed,
            "nodes": self.nodes,
            "channels": self.channels,
            "total_capacity_sat": self.total_capacity_sat,
            "values": [int(v) for v in self.values],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "MinCutSamples":
        """Inverse of :meth:`to_dict`."""
        values = np.asarray(payload["values"], dtype=np.int64)

        def field(name: str) -> int:
            return int(payload[name])  # type: ignore[call-overload,no-any-return]

        return cls(
            values=values,
            nodes=field("nodes"),
            channels=field("channels"),
            total_capacity_sat=field("total_capacity_sat"),
            trials=field("trials"),
            seed=field("seed"),
            label=str(payload.get("label", "")),
            scope=str(payload.get("scope", "")),
        )


class _FlowGraph:
    """A fixed channel topology whose liquidity state can be redrawn cheaply.

    The directed skeleton is built once; each trial only replaces the capacity vector.
    Directed edge ``i`` is ``a -> b`` and edge ``i + m`` is ``b -> a`` for channel ``i``.
    """

    def __init__(self, channels: Sequence[Channel]) -> None:
        self.node_ids: List[str] = sorted({n for a, b, _ in channels for n in (a, b)})
        index = {node: i for i, node in enumerate(self.node_ids)}
        self.n = len(self.node_ids)
        self.m = len(channels)
        self._src = np.fromiter((index[a] for a, _, _ in channels), dtype=np.int64, count=self.m)
        self._dst = np.fromiter((index[b] for _, b, _ in channels), dtype=np.int64, count=self.m)
        self.capacity = np.fromiter((c for _, _, c in channels), dtype=np.int64, count=self.m)
        self.total_capacity_sat = int(self.capacity.sum())

        if HAVE_IGRAPH:
            forward = list(zip(self._src.tolist(), self._dst.tolist(), strict=False))
            backward = list(zip(self._dst.tolist(), self._src.tolist(), strict=False))
            self._graph = _igraph.Graph(n=self.n, edges=forward + backward, directed=True)
        else:  # pragma: no cover - depends on what is installed
            self._graph = None

    def draw_state(self, rng: np.random.Generator) -> np.ndarray:
        """Draw one liquidity state: ``r ~ Uniform{0..c}`` per channel, other side ``c - r``."""
        forward = rng.integers(0, self.capacity + 1)
        return np.concatenate([forward, self.capacity - forward])

    def max_flow(self, state: np.ndarray, source: int, target: int) -> int:
        """Max-flow from ``source`` to ``target`` under a directed-capacity vector.

        ``state`` is laid out as the skeleton is: the first ``m`` entries are the
        ``a -> b`` directions, the next ``m`` the ``b -> a`` directions.
        """
        if HAVE_IGRAPH and self._graph is not None:
            return int(self._graph.maxflow_value(source, target, capacity=state.astype(float).tolist()))
        return self._max_flow_networkx(state, source, target)

    def _max_flow_networkx(self, state: np.ndarray, source: int, target: int) -> int:
        """Backend-independent fallback. Slower, but the same answer."""
        forward, backward = state[: self.m], state[self.m :]
        digraph = nx.DiGraph()
        digraph.add_nodes_from(range(self.n))
        for tails, heads, capacities in ((self._src, self._dst, forward), (self._dst, self._src, backward)):
            for tail, head, capacity in zip(tails.tolist(), heads.tolist(), capacities.tolist(), strict=False):
                if capacity > 0:
                    digraph.add_edge(tail, head, capacity=capacity)
        if not digraph.has_node(source) or not digraph.has_node(target):
            return 0
        return int(nx.maximum_flow_value(digraph, source, target))


def sample_min_cuts(
    channels: Sequence[Channel],
    trials: int = 10_000,
    seed: int = 0,
    label: str = "",
    scope: str = "",
    progress_every: int = 0,
) -> MinCutSamples:
    """Sample ``trials`` max-flows, each on a freshly drawn liquidity state.

    Every trial redraws the *whole* network's liquidity and a *new* random ordered pair
    of distinct nodes, so the samples are independent draws from the joint distribution
    over (state, payment pair) — which is what makes the resulting curve a statement
    about the network rather than about any particular pair.
    """
    if trials <= 0:
        raise ValueError(f"trials must be positive, got {trials}")
    if len(channels) == 0:
        raise ValueError("no channels with known capacity — attach capacities first")

    flow_graph = _FlowGraph(channels)
    if flow_graph.n < 2:
        raise ValueError(f"need at least 2 nodes to sample a payment pair, got {flow_graph.n}")

    rng = np.random.default_rng(seed)
    values = np.empty(trials, dtype=np.int64)
    for trial in range(trials):
        state = flow_graph.draw_state(rng)
        source, target = rng.choice(flow_graph.n, size=2, replace=False)
        values[trial] = flow_graph.max_flow(state, int(source), int(target))
        if progress_every and (trial + 1) % progress_every == 0:
            logger.info("%s/%s: %d/%d trials", label or "?", scope or "?", trial + 1, trials)

    return MinCutSamples(
        values=values,
        nodes=flow_graph.n,
        channels=flow_graph.m,
        total_capacity_sat=flow_graph.total_capacity_sat,
        trials=trials,
        seed=seed,
        label=label,
        scope=scope,
    )


def feasibility(values: np.ndarray, amount_sat: float) -> float:
    """Fraction of samples whose min-cut is at least ``amount_sat``.

    This is the headline quantity: an upper bound on the probability that a payment of
    ``amount_sat`` between two uniformly random nodes is possible at all.

    Note this is ``P(X >= a)``. The original notebook reports ``P(X > x)`` at the
    smallest sampled value ``x`` above ``a``, which differs by at most one sample's
    worth of probability (``1/trials``).
    """
    if len(values) == 0:
        return float("nan")
    return float(np.count_nonzero(values >= amount_sat) / len(values))


def feasibility_curve(values: np.ndarray, amounts_sat: Union[Sequence[float], np.ndarray]) -> np.ndarray:
    """:func:`feasibility` evaluated over many amounts (vectorised)."""
    sorted_values = np.sort(np.asarray(values))
    amounts = np.asarray(amounts_sat, dtype=float)
    # index of the first sample >= amount; everything from there up counts as feasible
    first_ok = np.searchsorted(sorted_values, amounts, side="left")
    return (len(sorted_values) - first_ok) / len(sorted_values)


def survival_curve(values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """The empirical curve through the sampled values themselves.

    Returns ``(x, y)`` with ``y[i] = P(min-cut >= x[i])`` over the distinct sampled
    values — the step function that :func:`feasibility` interpolates. Use this to plot
    without having to pick an amount grid.
    """
    if len(values) == 0:
        return np.empty(0), np.empty(0)
    x = np.unique(values)
    return x, feasibility_curve(values, x)


def amount_at_service_level(values: np.ndarray, probability: float) -> int:
    """Largest amount that is feasible in at least ``probability`` of the samples.

    The inverse reading of :func:`feasibility`: "if I want a 97.5% chance the payment is
    even possible, how much can I send?" Returns 0 when no positive amount clears the
    bar.
    """
    if not 0.0 < probability <= 1.0:
        raise ValueError(f"probability must be in (0, 1], got {probability}")
    if len(values) == 0:
        return 0
    sorted_values = np.sort(np.asarray(values))
    # we may drop at most floor((1 - p) * n) samples below the threshold
    droppable = int(np.floor((1.0 - probability) * len(sorted_values)))
    if droppable >= len(sorted_values):
        return int(sorted_values[-1])
    return int(sorted_values[droppable])


def service_level_table(
    values: np.ndarray,
    probabilities: Sequence[float] = (0.999, 0.99, 0.975, 0.9, 0.8, 0.5),
) -> Dict[float, int]:
    """:func:`amount_at_service_level` over a set of service-level objectives."""
    return {p: amount_at_service_level(values, p) for p in probabilities}


def exact_feasibility(channels: Sequence[Channel], source: str, target: str, amount_sat: int) -> float:
    """Exact ``P(max-flow >= amount)`` by enumerating **every** liquidity state.

    Only tractable for toy graphs — the state count is ``prod(c_i + 1)`` — but it is
    what :func:`sample_min_cuts` estimates, so it pins the sampler down in tests and
    reproduces the worked example from the original notebook.
    """
    from itertools import product

    total_states = 1
    for _, _, capacity in channels:
        total_states *= capacity + 1
    if total_states > 2_000_000:
        raise ValueError(f"{total_states} states is too many to enumerate; use sample_min_cuts")

    flow_graph = _FlowGraph(channels)
    index = {node: i for i, node in enumerate(flow_graph.node_ids)}
    if source not in index or target not in index:
        return 0.0

    feasible = 0
    for allocation in product(*(range(c + 1) for _, _, c in channels)):
        forward = np.asarray(allocation, dtype=np.int64)
        state = np.concatenate([forward, flow_graph.capacity - forward])
        if flow_graph.max_flow(state, index[source], index[target]) >= amount_sat:
            feasible += 1
    return feasible / total_states


def load_bos_nodes(path: str) -> Dict[str, int]:
    """Read a Bos-score file (``nodes.lightning.computer/availability/v3/btc.json``).

    Returns ``{public_key: score}``. A positive score is the study's proxy for "a
    professionally maintained node".
    """
    import json

    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return {entry["public_key"]: int(entry["score"]) for entry in payload.get("scores", [])}


def professional_nodes(bos_scores: Dict[str, int], present: Optional[Iterable[str]] = None) -> List[str]:
    """BOS-scored nodes, optionally intersected with the nodes present in a snapshot.

    The BOS list is only published for *today*, so applying it to a historical snapshot
    means asking "which of today's professional nodes already existed then?". The
    intersection shrinks as you go back; report its size alongside any result.
    """
    scored = {key for key, score in bos_scores.items() if score > 0}
    if present is None:
        return sorted(scored)
    return sorted(scored & set(present))
