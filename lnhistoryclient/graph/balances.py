"""Synthetic channel-balance assignment for a snapshot graph.

**BOLT #7 gossip never reveals channel balances.** A Lightning channel has a public
capacity split privately between its two ends, and only the two peers know the split.
Every balance produced here is therefore *invented* — a modelling assumption, not an
observation. Results computed on top of one are conditional on the assumption, which is
why :class:`BalanceScenario` records exactly how a graph was populated.

Storage model
-------------
The single source of truth is ``graph.graph["balances"][scid]`` — an integer giving
**node_1's share in satoshis**, where node_1 is the endpoint that BOLT #7's
``channel_announcement`` lists first (the lexicographically smaller public key, which is
also the source of the ``direction = 0`` edge). :func:`set_balance` mirrors it onto both
directed edges as ``local_balance_sat`` — the amount that edge's *source* can send — so
the invariant ``local(0) + local(1) == capacity`` holds by construction.

Distributions
-------------
A split is a fraction in ``[0, 1]``, so the natural family is Beta. The friendly names
are thin wrappers: :class:`Uniform` is ``Beta(1, 1)``, :class:`Polarised` is
``Beta(c, c)`` with ``c < 1``. Every distribution carries ``lo``/``hi`` clip bounds;
``lo=0.01, hi=0.99`` reproduces the ~1% ``channel_reserve_satoshis`` that LND and CLN
both default to, without needing a separate "reserve" concept.

Orientation — read this before interpreting a skewed distribution
-----------------------------------------------------------------
node_1 is the *lexicographically smaller pubkey*. That is cryptographically arbitrary:
it correlates with nothing — not degree, not capacity, not age, not who funded the
channel. Under the default :attr:`Orientation.LEXICOGRAPHIC`, skewing a distribution
toward node_1 is therefore **not** a systematic network bias; it is statistically
indistinguishable from picking a random side per channel, and ``Beta(2, 5)`` will
produce the same aggregates as ``Beta(5, 2)``.

For a *meaningful* asymmetry ("hubs hold the liquidity", "hubs are drained") pass
:attr:`Orientation.BY_DEGREE` or :attr:`Orientation.BY_CAPACITY`, which orient the split
toward an economically real property of the endpoints.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

import networkx as nx
import numpy as np

from lnhistoryclient.graph.projections import edge_capacity_sat

logger = logging.getLogger(__name__)

#: Graph-level key holding ``{scid: node_1_share_sat}`` — the single source of truth.
BALANCES_KEY = "balances"
#: Graph-level key holding the :class:`BalanceScenario` that produced the balances.
SCENARIO_KEY = "balance_scenario"
#: Directed-edge attribute: satoshis the edge's *source* can send over this channel.
LOCAL_BALANCE_ATTR = "local_balance_sat"


# ── distributions ────────────────────────────────────────────────────────────────


class BalanceDistribution(ABC):
    """A distribution over a channel's balance *fraction* in ``[lo, hi]``.

    Subclasses implement :meth:`_draw`; :meth:`sample` applies the clip bounds. Sampling
    is vectorised so assigning 50k channels is one numpy call rather than 50k Python
    calls.
    """

    def __init__(self, lo: float = 0.0, hi: float = 1.0) -> None:
        if not 0.0 <= lo <= hi <= 1.0:
            raise ValueError(f"clip bounds must satisfy 0 <= lo <= hi <= 1, got lo={lo}, hi={hi}")
        self.lo = float(lo)
        self.hi = float(hi)

    @abstractmethod
    def _draw(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Draw ``n`` raw fractions, before clipping."""

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        """Draw ``n`` fractions, clipped to ``[lo, hi]``."""
        if n <= 0:
            return np.empty(0, dtype=float)
        return np.clip(np.asarray(self._draw(n, rng), dtype=float), self.lo, self.hi)

    def _params(self) -> Dict[str, object]:
        """Parameters to include in ``repr`` (excluding the clip bounds)."""
        return {}

    def __repr__(self) -> str:
        parts = [f"{key}={value}" for key, value in self._params().items()]
        if self.lo != 0.0 or self.hi != 1.0:
            parts += [f"lo={self.lo}", f"hi={self.hi}"]
        return f"{type(self).__name__}({', '.join(parts)})"


class Balanced(BalanceDistribution):
    """Every channel split exactly 50/50 — the standard baseline scenario."""

    def _draw(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return np.full(n, 0.5, dtype=float)


class Uniform(BalanceDistribution):
    """Fraction drawn uniformly at random — maximum ignorance. Equivalent to ``Beta(1, 1)``."""

    def _draw(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.uniform(0.0, 1.0, size=n)


class Beta(BalanceDistribution):
    """``Beta(a, b)`` — the general family on ``[0, 1]``.

    ``a = b = 1`` is uniform, ``a = b > 1`` is a bell around 0.5, ``a = b < 1`` is
    U-shaped, and ``a != b`` skews (subject to the orientation caveat in the module
    docstring).
    """

    def __init__(self, a: float = 5.0, b: float = 5.0, lo: float = 0.0, hi: float = 1.0) -> None:
        super().__init__(lo, hi)
        if a <= 0 or b <= 0:
            raise ValueError(f"Beta shape parameters must be positive, got a={a}, b={b}")
        self.a = float(a)
        self.b = float(b)

    def _draw(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.beta(self.a, self.b, size=n)

    def _params(self) -> Dict[str, object]:
        return {"a": self.a, "b": self.b}


class Normal(BalanceDistribution):
    """Gaussian fractions, clipped into range.

    Note that clipping piles probability mass *at* the bounds rather than truncating
    the distribution: with ``sigma`` large relative to the range, a sizeable share of
    channels lands exactly on ``lo`` or ``hi``. Prefer :class:`Beta` when that matters.
    """

    def __init__(self, mu: float = 0.5, sigma: float = 0.2, lo: float = 0.0, hi: float = 1.0) -> None:
        super().__init__(lo, hi)
        if sigma < 0:
            raise ValueError(f"sigma must be non-negative, got {sigma}")
        self.mu = float(mu)
        self.sigma = float(sigma)

    def _draw(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.normal(self.mu, self.sigma, size=n)

    def _params(self) -> Dict[str, object]:
        return {"mu": self.mu, "sigma": self.sigma}


class Polarised(BalanceDistribution):
    """U-shaped ``Beta(c, c)`` with ``c < 1``: most channels nearly exhausted on one side.

    Empirically the most realistic single-parameter shape for a live network, where
    routing pushes channels toward their ends and few sit near 50/50.
    """

    def __init__(self, concentration: float = 0.3, lo: float = 0.0, hi: float = 1.0) -> None:
        super().__init__(lo, hi)
        if concentration <= 0:
            raise ValueError(f"concentration must be positive, got {concentration}")
        self.concentration = float(concentration)

    def _draw(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.beta(self.concentration, self.concentration, size=n)

    def _params(self) -> Dict[str, object]:
        return {"concentration": self.concentration}


class Custom(BalanceDistribution):
    """Escape hatch wrapping ``fn(n, rng) -> array of fractions``.

    Pass ``name`` so the :class:`BalanceScenario` records something more useful than
    ``<lambda>``.
    """

    def __init__(
        self,
        fn: Callable[[int, np.random.Generator], np.ndarray],
        lo: float = 0.0,
        hi: float = 1.0,
        name: str = "custom",
    ) -> None:
        super().__init__(lo, hi)
        self.fn = fn
        self.name = name

    def _draw(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return np.asarray(self.fn(n, rng), dtype=float)

    def _params(self) -> Dict[str, object]:
        return {"name": self.name}


# ── orientation ──────────────────────────────────────────────────────────────────


class Orientation(str, Enum):
    """Which endpoint the sampled fraction is assigned to.

    ``LEXICOGRAPHIC`` uses node_1 (BOLT #7 order). It is reproducible and matches the
    storage layout, but it is economically arbitrary — see the module docstring before
    reading meaning into a skewed distribution under it.
    """

    LEXICOGRAPHIC = "lexicographic"
    BY_DEGREE = "by_degree"
    BY_CAPACITY = "by_capacity"
    RANDOM = "random"


@dataclass(frozen=True)
class BalanceScenario:
    """A reproducible description of how a graph's balances were produced."""

    distribution: str
    seed: Optional[int]
    orientation: str
    correlate: bool
    n_channels: int
    n_assigned: int
    capacity_source: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# ── internals ────────────────────────────────────────────────────────────────────


def _channel_table(graph: nx.MultiDiGraph, strict: bool) -> Tuple[List[Tuple[int, str, str, int]], List[int], str]:
    """Collect ``(scid, node_1, node_2, capacity_sat)`` for every channel.

    Endpoints are read from the ``direction = 0`` edge, whose source is node_1 by
    construction in :func:`~lnhistoryclient.graph.builder.build_multidigraph`.

    Returns the usable channels, the scids that had no capacity, and a label describing
    where the capacities came from.
    """
    rows: List[Tuple[int, str, str, int]] = []
    missing: List[int] = []
    used_proxy = False
    used_on_chain = False

    for src, dst, key, attrs in graph.edges(keys=True, data=True):
        if attrs.get("direction") != 0:
            continue
        scid = key if isinstance(key, int) else attrs.get("scid")
        if not isinstance(scid, int):
            continue

        capacity = attrs.get("capacity_sat")
        if isinstance(capacity, int) and capacity > 0:
            used_on_chain = True
        elif strict:
            missing.append(scid)
            continue
        else:
            proxy = edge_capacity_sat(attrs)
            if not proxy or proxy <= 0:
                missing.append(scid)
                continue
            capacity = proxy
            used_proxy = True
        rows.append((scid, src, dst, int(capacity)))

    if used_on_chain and used_proxy:
        source = "mixed"
    elif used_proxy:
        source = "htlc_max_proxy"
    else:
        source = "on_chain"
    return rows, missing, source


def _first_is_node_1(
    graph: nx.MultiDiGraph,
    rows: List[Tuple[int, str, str, int]],
    orientation: Orientation,
    rng: np.random.Generator,
) -> np.ndarray:
    """Boolean mask: does the sampled fraction belong to node_1 for each channel?

    Ties fall back to lexicographic order so the result stays deterministic.
    """
    if orientation is Orientation.LEXICOGRAPHIC:
        return np.ones(len(rows), dtype=bool)
    if orientation is Orientation.RANDOM:
        return rng.random(len(rows)) < 0.5

    if orientation is Orientation.BY_DEGREE:
        score: Dict[str, float] = {node: float(deg) for node, deg in graph.degree()}
    else:  # BY_CAPACITY
        score = {node: 0.0 for node in graph.nodes()}
        for _scid, node_1, node_2, capacity in rows:
            score[node_1] = score.get(node_1, 0.0) + capacity
            score[node_2] = score.get(node_2, 0.0) + capacity

    # The fraction goes to the *higher-scoring* endpoint; node_1 wins ties (it sorts first).
    return np.array(
        [score.get(node_1, 0.0) >= score.get(node_2, 0.0) for _scid, node_1, node_2, _cap in rows],
        dtype=bool,
    )


def _correlated_fractions(
    rows: List[Tuple[int, str, str, int]],
    distribution: BalanceDistribution,
    rng: np.random.Generator,
) -> np.ndarray:
    """Fractions derived from one latent "fill level" per *node*.

    Independent per-channel sampling makes the network unrealistically well-mixed: a
    node with 20 channels almost surely has some flush channel in every direction, so
    simulated routability is systematically optimistic. Drawing one level per node and
    setting ``f_u / (f_u + f_v)`` makes a drained node drained across *all* its
    channels at once, which is how depletion actually behaves.
    """
    nodes = sorted({node for _scid, node_1, node_2, _cap in rows for node in (node_1, node_2)})
    levels = dict(zip(nodes, distribution.sample(len(nodes), rng), strict=False))

    fractions = np.empty(len(rows), dtype=float)
    for i, (_scid, node_1, node_2, _cap) in enumerate(rows):
        level_1, level_2 = levels[node_1], levels[node_2]
        total = level_1 + level_2
        fractions[i] = 0.5 if total <= 0 else level_1 / total
    return np.clip(fractions, distribution.lo, distribution.hi)


# ── public API ───────────────────────────────────────────────────────────────────


def _write_balance(
    graph: nx.MultiDiGraph, scid: int, node_1: str, node_2: str, capacity_sat: int, node_1_share_sat: int
) -> None:
    """Store one channel's balance and mirror it onto both directed edges. ``O(1)``.

    The single point where balance state is written. Both directions are updated
    together, so the ``local(0) + local(1) == capacity`` invariant cannot drift.
    """
    if not 0 <= node_1_share_sat <= capacity_sat:
        raise ValueError(f"balance {node_1_share_sat} outside [0, {capacity_sat}] for scid {scid}")

    graph.graph.setdefault(BALANCES_KEY, {})[scid] = int(node_1_share_sat)
    graph[node_1][node_2][scid][LOCAL_BALANCE_ATTR] = int(node_1_share_sat)
    graph[node_2][node_1][scid][LOCAL_BALANCE_ATTR] = int(capacity_sat - node_1_share_sat)


def set_balance(graph: nx.MultiDiGraph, scid: int, node_1_share_sat: int) -> None:
    """Set one channel's balance by scid, mirroring it onto both directed edges.

    Scans the edge list to resolve the channel's endpoints, so it is ``O(E)`` —
    intended for occasional manual adjustment. Bulk assignment
    (:func:`assign_balances`) and per-payment settlement (:func:`apply_balance_delta`)
    both use ``O(1)`` paths instead.
    """
    endpoints = next(
        ((u, v, a) for u, v, k, a in graph.edges(keys=True, data=True) if k == scid and a.get("direction") == 0),
        None,
    )
    if endpoints is None:
        raise KeyError(f"scid {scid} is not present in the graph")
    node_1, node_2, attrs = endpoints

    capacity = attrs.get("capacity_sat")
    if not isinstance(capacity, int):
        raise ValueError(f"scid {scid} has no capacity_sat; cannot split a balance without a capacity")
    _write_balance(graph, scid, node_1, node_2, capacity, node_1_share_sat)


def apply_balance_delta(graph: nx.MultiDiGraph, src: str, dst: str, scid: int, amount_sat: int) -> None:
    """Move ``amount_sat`` from ``src`` to ``dst`` across channel ``scid``.

    ``O(1)``: the caller already knows the endpoints, so this indexes straight into the
    two directed edges instead of scanning. Used by payment settlement.
    """
    forward = graph[src][dst][scid]
    reverse = graph[dst][src][scid]
    direction = forward.get("direction")

    balances = graph.graph.setdefault(BALANCES_KEY, {})
    current = balances.get(scid)
    if current is None:
        raise KeyError(f"scid {scid} has no assigned balance")

    # direction 0 flows node_1 -> node_2, which *reduces* node_1's share.
    balances[scid] = current - amount_sat if direction == 0 else current + amount_sat
    forward[LOCAL_BALANCE_ATTR] = int(forward.get(LOCAL_BALANCE_ATTR, 0)) - amount_sat
    reverse[LOCAL_BALANCE_ATTR] = int(reverse.get(LOCAL_BALANCE_ATTR, 0)) + amount_sat


def assign_balances(
    graph: nx.MultiDiGraph,
    distribution: Optional[BalanceDistribution] = None,
    *,
    seed: Optional[int] = None,
    orientation: Orientation = Orientation.LEXICOGRAPHIC,
    correlate: bool = False,
    strict: bool = True,
) -> BalanceScenario:
    """Populate every channel with a synthetic balance drawn from ``distribution``.

    Args:
        graph: The canonical ``MultiDiGraph``; mutated in place.
        distribution: Balance-fraction distribution (defaults to :class:`Balanced`).
        seed: Seed for reproducibility. Together with the distribution and orientation
            it reproduces the assignment exactly.
        orientation: Which endpoint the fraction is assigned to. Read the module
            docstring before interpreting a skewed distribution under the default.
        correlate: Draw one latent fill level per *node* instead of one fraction per
            channel, so a drained node is drained across all its channels.
        strict: Require a real on-chain ``capacity_sat``. When ``False``, fall back to
            the ``htlc_maximum_msat`` proxy.

    Returns:
        The :class:`BalanceScenario`, also stored at ``graph.graph["balance_scenario"]``.

    Raises:
        ValueError: In strict mode, when any channel lacks ``capacity_sat``.
    """
    distribution = distribution or Balanced()
    rng = np.random.default_rng(seed)

    rows, missing, capacity_source = _channel_table(graph, strict)
    if missing and strict:
        raise ValueError(
            f"{len(missing)} of {len(rows) + len(missing)} channels have no capacity_sat. "
            "Fetch it with get_snapshot(..., enrich_capacity=True) or attach_capacity(graph, caps), "
            "or pass strict=False to fall back to the htlc_maximum_msat proxy."
        )

    fractions = _correlated_fractions(rows, distribution, rng) if correlate else distribution.sample(len(rows), rng)
    to_node_1 = _first_is_node_1(graph, rows, orientation, rng)

    graph.graph[BALANCES_KEY] = {}
    for (scid, node_1, node_2, capacity), fraction, first_is_1 in zip(rows, fractions, to_node_1, strict=False):
        share = int(round(float(fraction) * capacity))
        if not first_is_1:
            share = capacity - share
        _write_balance(graph, scid, node_1, node_2, capacity, share)

    scenario = BalanceScenario(
        distribution=repr(distribution),
        seed=seed,
        orientation=orientation.value,
        correlate=correlate,
        n_channels=len(rows) + len(missing),
        n_assigned=len(rows),
        capacity_source=capacity_source,
    )
    graph.graph[SCENARIO_KEY] = scenario

    if missing:
        logger.warning(
            "assign_balances: %d channels had no capacity and were left unassigned (unroutable)", len(missing)
        )
    logger.info(
        "assign_balances: %d/%d channels assigned from %s (capacity: %s)",
        scenario.n_assigned,
        scenario.n_channels,
        scenario.distribution,
        capacity_source,
    )
    return scenario


# ── persistence ──────────────────────────────────────────────────────────────────


def save_balances(graph: nx.MultiDiGraph, path: str) -> None:
    """Write the current ``scid -> node_1 share`` map and its scenario to ``path``.

    Format is chosen by suffix: ``.csv``, ``.json``, or ``.parquet`` (needs ``pyarrow``,
    which is *not* part of the ``analysis`` extra).

    Balances live in ``graph.graph``, which does **not** survive GraphML or GML export
    (those permit only scalar graph attributes) and whose integer scid keys become
    strings under JSON node-link export. Use this instead of relying on a graph
    round-trip.
    """
    balances = graph.graph.get(BALANCES_KEY)
    if not balances:
        raise ValueError("graph has no balances; call assign_balances() first")
    scenario = graph.graph.get(SCENARIO_KEY)

    if path.endswith(".json"):
        payload = {
            "scenario": scenario.to_dict() if isinstance(scenario, BalanceScenario) else None,
            # JSON object keys must be strings; load_balances converts them back to int.
            "balances": {str(scid): int(sat) for scid, sat in balances.items()},
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    else:
        import pandas as pd

        frame = pd.DataFrame({"scid": list(balances.keys()), "balance_node_1_sat": list(balances.values())}).astype(
            {"scid": "int64", "balance_node_1_sat": "int64"}
        )
        if path.endswith(".parquet"):
            frame.to_parquet(path, index=False)
        elif path.endswith(".csv"):
            frame.to_csv(path, index=False)
        else:
            raise ValueError(f"unsupported balance file suffix: {path!r} (use .csv, .json or .parquet)")

    logger.info("save_balances: wrote %d channel balances to %s", len(balances), path)


def load_balances(graph: nx.MultiDiGraph, path: str) -> int:
    """Restore balances written by :func:`save_balances`; returns the number applied.

    Channels present in the file but absent from the graph are skipped and counted in
    the log — restoring a scenario onto a *different* snapshot is a common mistake and
    should not fail silently.
    """
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        pairs = [(int(scid), int(sat)) for scid, sat in payload["balances"].items()]
        scenario_dict = payload.get("scenario")
    else:
        import pandas as pd

        frame = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
        pairs = [(int(row.scid), int(row.balance_node_1_sat)) for row in frame.itertuples()]
        scenario_dict = None

    applied = skipped = 0
    for scid, sat in pairs:
        try:
            set_balance(graph, scid, sat)
            applied += 1
        except (KeyError, ValueError):
            skipped += 1

    if scenario_dict:
        graph.graph[SCENARIO_KEY] = BalanceScenario(**scenario_dict)
    if skipped:
        logger.warning("load_balances: %d/%d entries did not match this graph", skipped, len(pairs))
    logger.info("load_balances: applied %d channel balances from %s", applied, path)
    return applied
