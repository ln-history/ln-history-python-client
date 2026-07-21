"""Shared fixtures: small hand-built snapshot graphs with known structure."""

import pytest

from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate

# Three deterministic node ids.
NODE_A = "aa" * 33
NODE_B = "bb" * 33
NODE_C = "cc" * 33


def channel_announcement(scid: int, n1: str, n2: str) -> ChannelAnnouncement:
    return ChannelAnnouncement(
        features=b"",
        chain_hash=b"\x00" * 32,
        scid=scid,
        node_id_1=bytes.fromhex(n1),
        node_id_2=bytes.fromhex(n2),
        bitcoin_key_1=b"",
        bitcoin_key_2=b"",
        node_signature_1=b"",
        node_signature_2=b"",
        bitcoin_signature_1=b"",
        bitcoin_signature_2=b"",
    )


def channel_update(
    scid: int,
    direction: int,
    fee_base_msat: int = 1000,
    fee_ppm: int = 1,
    htlc_max_msat: int = 5_000_000_000,
    htlc_min_msat: int = 1000,
    disabled: bool = False,
    timestamp: int = 100,
) -> ChannelUpdate:
    flags = (0x02 if disabled else 0x00) | (direction & 0x01)
    return ChannelUpdate(
        signature=b"",
        chain_hash=b"\x00" * 32,
        scid=scid,
        timestamp=timestamp,
        message_flags=b"\x01",
        channel_flags=bytes([flags]),
        cltv_expiry_delta=40,
        htlc_minimum_msat=htlc_min_msat,
        fee_base_msat=fee_base_msat,
        fee_proportional_millionths=fee_ppm,
        htlc_maximum_msat=htlc_max_msat,
    )


@pytest.fixture
def line_graph_messages():
    """A─B─C path: two channels, both directions updated, symmetric policy."""
    return [
        channel_announcement(1, NODE_A, NODE_B),
        channel_announcement(2, NODE_B, NODE_C),
        channel_update(1, 0),
        channel_update(1, 1),
        channel_update(2, 0),
        channel_update(2, 1),
    ]
