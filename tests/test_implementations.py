"""Implementation fingerprinting from BOLT 9 feature bits."""

import pytest

from lnhistoryclient.analysis.implementations import (
    FAMILY,
    HEURISTICS,
    INDEFINITE,
    Feature,
    Heuristic,
    classify,
    decode,
    features_to_int,
    fingerprint,
    implementation,
    implementation_from_inbound_fee_tlv,
    matching_heuristics,
    unknown_bits,
    version_floor,
)

#: impscan's README works this example through by hand and reports CLN.
README_EXAMPLE = "800000080a6aa2"


# -- the published example -----------------------------------------------------------


def test_matches_impscans_own_worked_example() -> None:
    assert fingerprint(README_EXAMPLE) == "CLN"
    assert implementation(README_EXAMPLE) == "CLN"


def test_the_example_is_decided_by_ordering_not_uniqueness() -> None:
    """It satisfies two heuristics; CLN wins only because it is listed first.

    Worth pinning: it is the difference between "this is CLN" and "this is the first
    thing in a list that CLN happens to head".
    """
    matches = matching_heuristics(README_EXAMPLE)
    assert matches == ["CLN", "2200"]
    assert [h.name for h in HEURISTICS].index("CLN") < [h.name for h in HEURISTICS].index("2200")


# -- bitfield decoding ---------------------------------------------------------------


def test_accepts_bytes_hex_and_int_alike() -> None:
    as_int = int(README_EXAMPLE, 16)
    assert features_to_int(README_EXAMPLE) == as_int
    assert features_to_int(bytes.fromhex(README_EXAMPLE)) == as_int
    assert features_to_int(as_int) == as_int


def test_empty_bitfield_is_zero_not_an_error() -> None:
    assert features_to_int(b"") == 0
    assert features_to_int("") == 0


def test_rejects_a_type_it_cannot_read() -> None:
    with pytest.raises(TypeError):
        features_to_int(3.5)


def test_even_bit_is_required_and_odd_is_optional() -> None:
    """BOLT 9 numbers features in pairs; getting this backwards inverts every heuristic."""
    assert decode(1 << 0) == {"OPTION_DATA_LOSS_PROTECT": "mandatory"}
    assert decode(1 << 1) == {"OPTION_DATA_LOSS_PROTECT": "optional"}


def test_unknown_bits_are_reported_not_swallowed() -> None:
    assert unknown_bits(1 << 1000) == [1000]
    assert unknown_bits(1 << 1) == []


# -- requirement semantics -----------------------------------------------------------


def test_mandatory_and_optional_read_the_right_bit_of_the_pair() -> None:
    even, odd = 1 << 20, 1 << 21  # OPTION_ANCHOR_OUTPUTS
    assert Heuristic("m", OPTION_ANCHOR_OUTPUTS=Feature.MANDATORY).test(even)
    assert not Heuristic("m", OPTION_ANCHOR_OUTPUTS=Feature.MANDATORY).test(odd)
    assert Heuristic("o", OPTION_ANCHOR_OUTPUTS=Feature.OPTIONAL).test(odd)
    assert not Heuristic("o", OPTION_ANCHOR_OUTPUTS=Feature.OPTIONAL).test(even)


def test_not_set_rejects_either_bit_of_the_pair() -> None:
    rule = Heuristic("n", OPTION_ANCHOR_OUTPUTS=Feature.NOT_SET)
    assert rule.test(0)
    assert not rule.test(1 << 20)
    assert not rule.test(1 << 21)


def test_set_is_inert_by_default_and_enforced_under_strict() -> None:
    """impscan declares Feature.SET and never tests it -- there is no branch for it.

    Reproduced rather than silently corrected, because this module's job is to agree with
    impscan. ``strict`` opts into the reading the name implies.
    """
    rule = Heuristic("s", OPTION_ANCHOR_OUTPUTS=Feature.SET)
    assert rule.test(0) is True
    assert rule.test(0, strict=True) is False
    assert rule.test(1 << 20, strict=True) is True
    assert rule.test(1 << 21, strict=True) is True


def test_not_optional_matches_impscans_reading_of_the_even_bit() -> None:
    """impscan's NOT_OPTIONAL inspects the even bit, making it a duplicate of
    NOT_MANDATORY. No shipped heuristic uses it, so this is latent -- but pinned."""
    rule = Heuristic("x", OPTION_ANCHOR_OUTPUTS=Feature.NOT_OPTIONAL)
    assert rule.test(1 << 21) is True          # odd bit ignored, as impscan does
    assert rule.test(1 << 21, strict=True) is False
    assert rule.test(1 << 20) is False


# -- families ------------------------------------------------------------------------


def test_negative_heuristics_name_nobody() -> None:
    """Two heuristics match on a feature's ABSENCE. Reporting them as an implementation
    would attribute a quarter of the network to whichever one absorbed them."""
    assert FAMILY["No OPTION_SHUTDOWN_ANYSEGWIT"] == "Unknown"
    assert FAMILY["2200"] == "Unknown"
    assert FAMILY[INDEFINITE] == "Unknown"


def test_every_heuristic_has_a_family() -> None:
    for heuristic in HEURISTICS:
        assert heuristic.name in FAMILY, f"{heuristic.name} has no family mapping"


def test_classify_agrees_with_its_parts() -> None:
    name, family = classify(README_EXAMPLE)
    assert name == fingerprint(README_EXAMPLE)
    assert family == implementation(README_EXAMPLE)


def test_an_empty_bitfield_identifies_nobody() -> None:
    assert implementation(0) == "Unknown"


# -- the inbound-fee TLV -------------------------------------------------------------


def test_inbound_fee_tlv_is_one_directional() -> None:
    """Emitting lnd's TLV 55555 implies lnd; not emitting it implies nothing at all.

    Measured on the archive: 99.4% of emitters fingerprint as LND independently, but only
    7.9% of LND nodes emit it. Returning None rather than "not LND" keeps a caller from
    reading absence as evidence.
    """
    assert implementation_from_inbound_fee_tlv(True) == "LND"
    assert implementation_from_inbound_fee_tlv(False) is None
    assert implementation_from_inbound_fee_tlv(None) is None


# -- version floors ------------------------------------------------------------------

ORIGINS = {
    "LND": {
        24: ("v0.18.0-beta", "2024-05-30"),  # route blinding, a LOW bit from a LATE release
        25: ("v0.18.0-beta", "2024-05-30"),
        54: ("v0.15.0-beta", "2022-06-28"),  # keysend, a HIGH bit from an EARLY release
        55: ("v0.15.0-beta", "2022-06-28"),
    },
    "LDK": {30: ("v0.0.116", "2023-03-22")},
}


def test_floor_ranks_by_release_date_not_bit_number() -> None:
    """The case that makes the distinction real rather than pedantic.

    A node advertising both keysend (bit 54, v0.15.0) and route blinding (bit 24, v0.18.0)
    is at least v0.18.0. Ranking by bit number would answer v0.15.0 and be wrong.
    """
    both = (1 << 55) | (1 << 25)
    version, released, bit = version_floor(both, "LND", ORIGINS)
    assert version == "v0.18.0-beta"
    assert bit == 25


def test_floor_is_none_when_nothing_is_known() -> None:
    assert version_floor(0, "LND", ORIGINS) is None
    assert version_floor(1 << 55, "Eclair", ORIGINS) is None


def test_floor_is_read_against_one_implementations_numbering() -> None:
    """Bit 30 is option_amp to lnd and taproot to LDK, so the same bitfield must not
    resolve against the wrong implementation's table."""
    assert version_floor(1 << 30, "LDK", ORIGINS)[0] == "v0.0.116"
    assert version_floor(1 << 30, "LND", ORIGINS) is None
