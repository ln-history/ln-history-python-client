"""Distribution-concentration helpers (Gini, Lorenz curve, top-percentile share).

Ported from the former ``centrality_analysis.py`` so the metrics are reusable outside
the plotting script. They operate on any 1-D array of non-negative scores (betweenness,
capacity, degree, …).
"""

from typing import Sequence, Tuple

import numpy as np


def gini(scores: Sequence[float]) -> float:
    """Gini coefficient of a distribution of non-negative scores (0 = equal, →1 = concentrated)."""
    s = np.sort(np.asarray(scores, dtype=float))
    n = len(s)
    total = s.sum()
    if n == 0 or total == 0:
        return 0.0
    idx = np.arange(1, n + 1)
    return float((2 * idx - n - 1).dot(s) / (n * total))


def lorenz_xy(scores: Sequence[float]) -> Tuple[np.ndarray, np.ndarray]:
    """Return the Lorenz curve ``(x, y)`` points, each prefixed with the origin ``(0, 0)``.

    ``x`` is the cumulative share of nodes, ``y`` the cumulative share of total score.
    """
    s = np.sort(np.asarray(scores, dtype=float))
    n = len(s)
    if n == 0:
        return np.array([0.0]), np.array([0.0])
    cum = np.cumsum(s)
    x = np.insert(np.arange(1, n + 1) / n, 0, 0.0)
    y = np.insert(cum / cum[-1] if cum[-1] > 0 else cum, 0, 0.0)
    return x, y


def top_pct_share(scores: Sequence[float], pct: float) -> float:
    """Fraction of the total score held by the top ``pct`` percent of nodes."""
    s = np.asarray(scores, dtype=float)
    total = s.sum()
    if total == 0 or len(s) == 0:
        return 0.0
    k = max(1, int(np.ceil(len(s) * pct / 100)))
    return float(np.sort(s)[-k:].sum() / total)
