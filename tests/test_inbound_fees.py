"""lnd inbound fees (TLV record 55555): decoding, graph resolution, and pricing."""

import struct

import networkx as nx
import pytest

from lnhistoryclient.analysis.weights import (
    MAX_INBOUND_FEE_RATE_PPM,
    hop_fee_msat,
    inbound_fee_msat,
    route_fee_msat,
)
from lnhistoryclient.graph.builder import build_multidigraph
from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.parser.parser import parse_channel_update, parse_inbound_fees

TLV_PREFIX = b"\xfd\xd9\x03\x08"


def _tail(base: int, rate: int) -> bytes:
    return TLV_PREFIX + struct.pack(">i", base) + struct.pack(">i", rate)


# ── decoding ────────────────────────────────────────────────────────────────────────


def test_decodes_a_discount() -> None:
    assert parse_inbound_fees(_tail(-500, -2000)) == (-500, -2000)


def test_decodes_a_positive_inbound_fee() -> None:
    """lnd gates *setting* these behind a flag, but they occur on the wire."""
    assert parse_inbound_fees(_tail(1000, 250)) == (1000, 250)


def test_explicit_zero_is_not_absent() -> None:
    """(0, 0) means the record is present and zero; None means no record at all.

    The distinction is an implementation fingerprint -- lnd >= 0.18 with no inbound fee
    configured emits (0, 0) -- so collapsing them loses real information.
    """
    assert parse_inbound_fees(_tail(0, 0)) == (0, 0)
    assert parse_inbound_fees(b"") == (None, None)


@pytest.mark.parametrize(
    "tail",
    [
        b"",
        b"\xfd\xd9\x03\x08\x00\x00",  # truncated payload
        b"\xfd\xd9\x03\x09" + b"\x00" * 8,  # wrong length byte
        b"\xfd\xd9\x04\x08" + b"\x00" * 8,  # wrong record type
        b"\x01\x02\x03\x04" + b"\x00" * 8,  # not a TLV at all
    ],
)
def test_guard_yields_none_never_a_wrong_number(tail: bytes) -> None:
    """A guard miss must be None. A plausible-looking wrong fee is worse than no fee."""
    assert parse_inbound_fees(tail) == (None, None)


def test_extremes_round_trip_as_signed_int32() -> None:
    for value in (-2_147_483_648, -1, 0, 1, 2_147_483_647):
        assert parse_inbound_fees(_tail(value, value)) == (value, value)


# ── parse_channel_update integration ────────────────────────────────────────────────


def _channel_update_bytes(with_tlv: bool) -> bytes:
    payload = (
        b"\x00" * 64  # signature
        + b"\x01" * 32  # chain_hash
        + struct.pack(">Q", 123456789)  # scid
        + struct.pack(">I", 1700000000)  # timestamp
        + b"\x01"  # message_flags: htlc_maximum present
        + b"\x00"  # channel_flags
        + struct.pack(">H", 40)  # cltv_expiry_delta
        + struct.pack(">Q", 1000)  # htlc_minimum_msat
        + struct.pack(">I", 1000)  # fee_base_msat
        + struct.pack(">I", 100)  # fee_proportional_millionths
        + struct.pack(">Q", 10_000_000)  # htlc_maximum_msat
    )
    return payload + (_tail(-300, -1500) if with_tlv else b"")


def test_channel_update_carries_inbound_fees() -> None:
    update = parse_channel_update(_channel_update_bytes(with_tlv=True))
    assert update.fee_base_msat == 1000
    assert update.inbound_fee_base_msat == -300
    assert update.inbound_fee_proportional_millionths == -1500


def test_channel_update_without_tlv_is_none_not_zero() -> None:
    update = parse_channel_update(_channel_update_bytes(with_tlv=False))
    assert update.inbound_fee_base_msat is None
    assert update.inbound_fee_proportional_millionths is None


def test_stream_input_does_not_consume_the_tail() -> None:
    """Given a shared stream we cannot know where the message ends.

    Reading the tail anyway would eat the following message, so the stream path leaves
    the fields unset. ``parse_channel_update_extended`` is the stream-aware entry point.
    """
    import io

    stream = io.BytesIO(_channel_update_bytes(with_tlv=True))
    update = parse_channel_update(stream)
    assert update.inbound_fee_base_msat is None
    assert stream.read(4) == TLV_PREFIX  # tail still there for the caller


# ── graph resolution ────────────────────────────────────────────────────────────────


def _announcement(scid: int, node1: bytes, node2: bytes) -> ChannelAnnouncement:
    return ChannelAnnouncement(
        features=b"",
        chain_hash=b"\x01" * 32,
        scid=scid,
        node_id_1=node1,
        node_id_2=node2,
        bitcoin_key_1=b"\x02" * 33,
        bitcoin_key_2=b"\x03" * 33,
        node_signature_1=b"",
        node_signature_2=b"",
        bitcoin_signature_1=b"",
        bitcoin_signature_2=b"",
    )


def _update(scid: int, direction: int, inbound_base: int, inbound_rate: int) -> ChannelUpdate:
    return ChannelUpdate(
        signature=b"",
        chain_hash=b"\x01" * 32,
        scid=scid,
        timestamp=1700000000,
        message_flags=b"\x01",
        channel_flags=bytes([direction]),
        cltv_expiry_delta=40,
        htlc_minimum_msat=1000,
        fee_base_msat=1000,
        fee_proportional_millionths=100,
        htlc_maximum_msat=10_000_000,
        inbound_fee_base_msat=inbound_base,
        inbound_fee_proportional_millionths=inbound_rate,
    )


def test_inbound_fee_is_resolved_from_the_reverse_edge() -> None:
    """The routing-relevant inbound fee for u->v is advertised by v, on edge v->u.

    A channel_update is authored by one node and states that node's policy, so the
    inbound fee in the u->v update belongs to u. Verified against the archive: a
    distinctive setting traces to a single author 42.2% of the time against 6.8% for a
    single peer.
    """
    node1, node2 = b"\xaa" * 33, b"\xbb" * 33
    messages = [
        _announcement(1, node1, node2),
        _update(1, 0, -111, -1),  # authored by node1
        _update(1, 1, -222, -2),  # authored by node2
    ]
    graph = build_multidigraph(messages, inbound_fees=True)
    u, v = node1.hex(), node2.hex()

    # Travelling u -> v, the charge is v's, which v advertised on its own edge v -> u.
    assert graph[u][v][1]["dst_inbound_fee_base_msat"] == -222
    assert graph[v][u][1]["dst_inbound_fee_base_msat"] == -111
    # The raw per-edge value stays the author's own advertisement.
    assert graph[u][v][1]["inbound_fee_base_msat"] == -111


def test_flag_defaults_off_and_leaves_the_graph_unchanged() -> None:
    node1, node2 = b"\xaa" * 33, b"\xbb" * 33
    messages = [_announcement(1, node1, node2), _update(1, 0, -111, -1), _update(1, 1, -222, -2)]
    graph = build_multidigraph(messages)
    edge = graph[node1.hex()][node2.hex()][1]
    assert "dst_inbound_fee_base_msat" not in edge
    assert edge["inbound_fee_base_msat"] == -111  # raw value is always available


# ── pricing ─────────────────────────────────────────────────────────────────────────

OUTGOING = {"fee_base_msat": 1000, "fee_proportional_millionths": 100}


def test_hop_fee_adds_a_discount_to_the_outbound_fee() -> None:
    incoming = {"dst_inbound_fee_base_msat": -500, "dst_inbound_fee_proportional_millionths": -50}
    assert hop_fee_msat(None, OUTGOING, 100_000) == 11_000
    assert hop_fee_msat(incoming, OUTGOING, 100_000) == 5_500


def test_hop_fee_clamps_the_sum_at_zero_not_each_part() -> None:
    """lnd clamps signedFee = inbound + outbound, because Dijkstra cannot take a
    negative edge weight. A discount can cancel a fee but never pay the sender."""
    incoming = {"dst_inbound_fee_base_msat": -10_000_000, "dst_inbound_fee_proportional_millionths": 0}
    assert hop_fee_msat(incoming, OUTGOING, 100_000) == 0


def test_first_hop_charges_no_inbound_fee() -> None:
    assert hop_fee_msat(None, OUTGOING, 100_000) == fee_only(100_000)


def fee_only(amount_sat: int) -> int:
    return 1000 + (amount_sat * 1000 * 100) // 1_000_000


def test_missing_attributes_price_exactly_as_before() -> None:
    """An un-opted-in graph has no dst_inbound_* keys and must be unaffected."""
    assert hop_fee_msat({}, OUTGOING, 100_000) == fee_only(100_000)
    assert inbound_fee_msat({}, 100_000) == 0


def test_rate_is_clamped_to_lnd_maximum() -> None:
    absurd = {"dst_inbound_fee_base_msat": 0, "dst_inbound_fee_proportional_millionths": 10**9}
    expected = (100_000 * 1000 * MAX_INBOUND_FEE_RATE_PPM) // 1_000_000
    assert inbound_fee_msat(absurd, 100_000) == expected


def test_route_fee_prices_intermediate_hops_only() -> None:
    """Fees accrue at every node except the sender and the recipient."""
    discount = {
        "fee_base_msat": 0,
        "fee_proportional_millionths": 0,
        "dst_inbound_fee_base_msat": -500,
        "dst_inbound_fee_proportional_millionths": -50,
    }
    edges = [discount, discount, OUTGOING]
    assert route_fee_msat(edges, 100_000, inbound=False) == 11_000
    assert route_fee_msat(edges, 100_000, inbound=True) == 5_500
    # A single-edge route is a direct payment: no forwarding node, no fee.
    assert route_fee_msat([OUTGOING], 100_000) == 0


def test_graph_and_pricing_compose() -> None:
    """End to end: build with the flag, then price a two-hop route through the middle."""
    a, b, c = b"\xaa" * 33, b"\xbb" * 33, b"\xcc" * 33
    messages = [
        _announcement(1, a, b),
        _update(1, 0, 0, 0),
        _update(1, 1, -1000, 0),  # b discounts inbound on the a-b channel
        _announcement(2, b, c),
        _update(2, 0, 0, 0),
        _update(2, 1, 0, 0),
    ]
    graph = build_multidigraph(messages, inbound_fees=True)
    first = graph[a.hex()][b.hex()][1]
    second = graph[b.hex()][c.hex()][2]
    # b charges its outbound fee on channel 2 minus its inbound discount on channel 1.
    assert hop_fee_msat(first, second, 100_000) == fee_only(100_000) - 1000
    assert isinstance(graph, nx.MultiDiGraph)
