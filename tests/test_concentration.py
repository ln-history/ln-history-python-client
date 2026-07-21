"""Unit tests for concentration metrics."""

import numpy as np
import pytest

from lnhistoryclient.analysis.concentration import gini, lorenz_xy, top_pct_share


def test_gini_of_equal_distribution_is_zero():
    assert gini([5, 5, 5, 5]) == pytest.approx(0.0)


def test_gini_of_total_concentration_approaches_one():
    # one node holds everything
    assert gini([0, 0, 0, 100]) == pytest.approx(0.75)  # (n-1)/n for n=4


def test_gini_empty_and_all_zero_are_zero():
    assert gini([]) == 0.0
    assert gini([0, 0, 0]) == 0.0


def test_top_pct_share_bounds():
    scores = [1, 1, 1, 1, 96]
    # top 20% (1 of 5 nodes) holds 96/100
    assert top_pct_share(scores, 20) == pytest.approx(0.96)


def test_top_pct_share_all_zero():
    assert top_pct_share([0, 0], 50) == 0.0


def test_lorenz_starts_at_origin_ends_at_one():
    x, y = lorenz_xy([1, 2, 3, 4])
    assert x[0] == 0.0 and y[0] == 0.0
    assert x[-1] == pytest.approx(1.0)
    assert y[-1] == pytest.approx(1.0)


def test_lorenz_empty():
    x, y = lorenz_xy([])
    assert np.array_equal(x, [0.0]) and np.array_equal(y, [0.0])
