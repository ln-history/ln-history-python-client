"""Analytics over Lightning Network snapshot graphs.

This subpackage provides node ranking / centrality (:mod:`centrality`), payment
routing and simulation (:mod:`routing`, :mod:`payments`), distribution-concentration
helpers (:mod:`concentration`), and the single source of truth for edge-weight
conventions (:mod:`weights`).

Requires the ``analysis`` extra: ``pip install lnhistoryclient[analysis]``.
"""

from lnhistoryclient.analysis.centrality import Metric, top_nodes_by
from lnhistoryclient.analysis.concentration import gini, lorenz_xy, top_pct_share
from lnhistoryclient.analysis.weights import DEFAULT_AMOUNT_SAT, Weighting

__all__ = [
    "Metric",
    "top_nodes_by",
    "Weighting",
    "DEFAULT_AMOUNT_SAT",
    "gini",
    "lorenz_xy",
    "top_pct_share",
]
