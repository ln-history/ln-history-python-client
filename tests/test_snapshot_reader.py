"""Unit tests for the adaptive snapshot-stream reader.

The backend snapshot stream has inconsistent framing (some blobs carry a varint length
prefix, some do not). These tests build small synthetic streams and confirm the reader
handles both framings and resynchronises past stray bytes.
"""

import struct

from lnhistoryclient.api.requester import iter_snapshot_messages
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement
from lnhistoryclient.parser.common import varint_encode


def _channel_update_bytes(scid: int = 12345, direction: int = 0) -> bytes:
    """A minimal type-258 channel_update message (type ++ payload), no varint frame."""
    flags = direction & 0x01
    return (
        struct.pack(">H", 258)
        + b"\x00" * 64  # signature
        + b"\x00" * 32  # chain_hash
        + struct.pack(">Q", scid)
        + struct.pack(">I", 1700000000)  # timestamp
        + b"\x00"  # message_flags (bit0 clear -> no htlc_maximum_msat)
        + bytes([flags])  # channel_flags
        + struct.pack(">H", 40)  # cltv_expiry_delta
        + struct.pack(">Q", 1000)  # htlc_minimum_msat
        + struct.pack(">I", 1000)  # fee_base_msat
        + struct.pack(">I", 10)  # fee_proportional_millionths
    )


def _node_announcement_bytes(node_byte: int = 0x02) -> bytes:
    """A minimal type-257 node_announcement (type ++ payload), no addresses/features."""
    node_id = bytes([node_byte]) + b"\x11" * 32
    return (
        struct.pack(">H", 257)
        + b"\x00" * 64  # signature
        + struct.pack(">H", 0)  # features len 0
        + struct.pack(">I", 1700000000)  # timestamp
        + node_id  # 33 bytes
        + b"\x00\x00\x00"  # rgb
        + b"\x00" * 32  # alias
        + struct.pack(">H", 0)  # addrlen 0
    )


def _write(tmp_path, data: bytes) -> str:
    path = tmp_path / "snap.bin"
    path.write_bytes(data)
    return str(path)


def test_reads_unframed_messages(tmp_path):
    stream = _channel_update_bytes(scid=1) + _node_announcement_bytes()
    msgs = list(iter_snapshot_messages(_write(tmp_path, stream)))
    assert [type(m).__name__ for m in msgs] == ["ChannelUpdate", "NodeAnnouncement"]
    assert isinstance(msgs[1], NodeAnnouncement)


def test_reads_varint_framed_messages(tmp_path):
    cu = _channel_update_bytes(scid=7)
    framed = varint_encode(len(cu) - 2) + cu  # varint(payload_len) ++ type ++ payload
    msgs = list(iter_snapshot_messages(_write(tmp_path, framed)))
    assert len(msgs) == 1 and isinstance(msgs[0], ChannelUpdate)
    assert msgs[0].scid == 7


def test_resyncs_past_stray_bytes(tmp_path):
    # A stray 0xff byte between two valid unframed messages must not derail parsing.
    stream = _channel_update_bytes(scid=1) + b"\xff" + _channel_update_bytes(scid=2)
    msgs = list(iter_snapshot_messages(_write(tmp_path, stream)))
    scids = sorted(m.scid for m in msgs if isinstance(m, ChannelUpdate))
    assert scids == [1, 2]


def test_mixed_framing_stream(tmp_path):
    cu = _channel_update_bytes(scid=9)
    stream = varint_encode(len(cu) - 2) + cu + _node_announcement_bytes()  # framed + unframed
    msgs = list(iter_snapshot_messages(_write(tmp_path, stream)))
    assert {type(m).__name__ for m in msgs} == {"ChannelUpdate", "NodeAnnouncement"}
