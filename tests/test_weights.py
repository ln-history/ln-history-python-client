"""Unit tests for the edge-weight conventions (the one place bugs hide silently)."""

import pytest

from lnhistoryclient.analysis.weights import (
    DEFAULT_AMOUNT_SAT,
    MISSING_CAPACITY_DISTANCE,
    Weighting,
    coerce_weighting,
    edge_strength,
    fee_msat,
    path_distance,
)


def test_coerce_weighting_accepts_none_str_enum():
    assert coerce_weighting(None) is Weighting.NONE
    assert coerce_weighting("fee") is Weighting.FEE
    assert coerce_weighting("CAPACITY") is Weighting.CAPACITY
    assert coerce_weighting(Weighting.NONE) is Weighting.NONE


def test_fee_msat_base_plus_proportional():
    # base 1000 msat + 1_000_000 sat * 500 ppm = 1000 + 500_000 msat
    attrs = {"fee_base_msat": 1000, "fee_proportional_millionths": 500}
    assert fee_msat(attrs, amount_sat=1_000_000) == 1000 + 500_000


def test_fee_msat_missing_fields_default_zero():
    assert fee_msat({}, amount_sat=100_000) == 0


def test_path_distance_none_is_hop_count():
    assert path_distance({}, Weighting.NONE) == 1.0


def test_path_distance_fee_uses_amount():
    attrs = {"fee_base_msat": 0, "fee_proportional_millionths": 1_000_000}  # 100% fee
    # 1 sat = 1000 msat forwarded at 100% -> 1000 msat
    assert path_distance(attrs, Weighting.FEE, amount_sat=1) == 1000.0


def test_path_distance_capacity_is_inverted():
    attrs = {"capacity_sat": 1000}
    assert path_distance(attrs, Weighting.CAPACITY) == pytest.approx(1 / 1000)


def test_path_distance_capacity_falls_back_to_htlc_proxy():
    attrs = {"capacity_sat": None, "htlc_maximum_msat": 2_000_000}  # 2000 sat proxy
    assert path_distance(attrs, Weighting.CAPACITY) == pytest.approx(1 / 2000)


def test_path_distance_capacity_missing_is_large():
    attrs = {"capacity_sat": None, "htlc_maximum_msat": None}
    assert path_distance(attrs, Weighting.CAPACITY) == MISSING_CAPACITY_DISTANCE


def test_edge_strength_capacity_is_raw():
    assert edge_strength({"capacity_sat": 5000}, Weighting.CAPACITY) == 5000.0


def test_edge_strength_none_is_one():
    assert edge_strength({}, Weighting.NONE) == 1.0


def test_edge_strength_fee_rejected():
    with pytest.raises(ValueError):
        edge_strength({}, Weighting.FEE)


def test_default_amount_is_100k():
    assert DEFAULT_AMOUNT_SAT == 100_000
