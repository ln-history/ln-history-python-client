"""Unit tests for the adaptive snapshot-stream reader.

The backend snapshot stream has inconsistent framing. Three shapes occur: framed
(``varint ++ type ++ payload``), unframed (``type ++ payload``), and type-less
(``varint ++ payload``). These tests build small synthetic streams and confirm the reader
handles all three and resynchronises past stray bytes.

The type-less tests matter out of proportion to that shape's frequency. Measured against
the database on the 2026-07-01 snapshot, dropping those blobs cost 12.3% of all live
policies although they are only 4.4% of them — because a dropped blob sends the reader
into a byte-at-a-time resync that then eats well-formed neighbours. So several tests here
assert on what surrounds a type-less blob, not just on the blob itself.
"""

from lnhistoryclient.api.requester import iter_snapshot_messages
from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement
from lnhistoryclient.parser.common import varint_encode
from lnhistoryclient.testing import (
    make_channel_announcement,
    make_channel_update,
    make_node_announcement,
    varint_payload_envelope,
)


def _channel_update_bytes(scid: int = 12345, direction: int = 0, htlc_max: bool = False) -> bytes:
    """A type-258 channel_update message (type ++ payload), no varint frame."""
    return make_channel_update(scid=scid, direction=direction, htlc_maximum_msat=16_000_000_000 if htlc_max else None)


def _channel_announcement_bytes(scid: int = 4242) -> bytes:
    """A type-256 channel_announcement (type ++ payload), no features."""
    return make_channel_announcement(scid=scid)


# Re-frame a channel_update the way the archive's damaged blobs are framed.
_typeless = varint_payload_envelope


def _node_announcement_bytes(node_byte: int = 0x02) -> bytes:
    """A type-257 node_announcement (type ++ payload), no addresses/features."""
    return make_node_announcement(node_seed=node_byte)


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


def test_reads_typeless_channel_update(tmp_path):
    """varint(128) ++ payload, the 2-byte type stripped — the archive's third shape."""
    blob = _typeless(_channel_update_bytes(scid=11))
    assert blob[0] == 128  # varint equals the payload length exactly
    msgs = list(iter_snapshot_messages(_write(tmp_path, blob)))
    assert len(msgs) == 1 and isinstance(msgs[0], ChannelUpdate)
    assert msgs[0].scid == 11


def test_reads_typeless_channel_update_carrying_htlc_maximum(tmp_path):
    """The 136-byte variant: message_flags bit 0 set, so the payload is 8 bytes longer."""
    blob = _typeless(_channel_update_bytes(scid=13, htlc_max=True))
    assert blob[0] == 136
    msgs = list(iter_snapshot_messages(_write(tmp_path, blob)))
    assert len(msgs) == 1 and msgs[0].scid == 13
    assert msgs[0].htlc_maximum_msat == 16_000_000_000


def test_typeless_update_does_not_swallow_its_neighbours(tmp_path):
    """The regression that made the loss 2.8x worse than the corrupt blobs alone.

    Before the type-less branch existed, the middle blob sent the reader into a
    byte-at-a-time resync that also destroyed well-formed messages after it.
    """
    stream = (
        _channel_announcement_bytes(scid=1)
        + _typeless(_channel_update_bytes(scid=2))
        + _channel_announcement_bytes(scid=3)
        + _channel_update_bytes(scid=4)
    )
    msgs = list(iter_snapshot_messages(_write(tmp_path, stream)))
    assert sorted(m.scid for m in msgs if isinstance(m, ChannelAnnouncement)) == [1, 3]
    assert sorted(m.scid for m in msgs if isinstance(m, ChannelUpdate)) == [2, 4]


def test_typeless_branch_requires_an_exact_length_match(tmp_path):
    """A varint that disagrees with message_flags must not be consumed as a payload.

    This is the guard that keeps the branch safe. Tolerating a mismatch here lets it
    match arbitrary byte runs: measured against the archive, allowing a 12-byte tail lost
    18 real channel_announcements and a 32-byte tail lost 137.
    """
    payload = _channel_update_bytes(scid=99)[2:]  # 128 bytes, message_flags bit0 clear
    blob = varint_encode(136) + payload + b"\x00" * 8  # varint claims the 136-byte shape
    followed_by = _channel_announcement_bytes(scid=5)
    msgs = list(iter_snapshot_messages(_write(tmp_path, blob + followed_by)))
    # The mismatched blob is skipped, but the message after it must still be found.
    assert [m.scid for m in msgs if isinstance(m, ChannelAnnouncement)] == [5]


def test_typeless_branch_never_preempts_a_well_framed_blob(tmp_path):
    """Ordering: the two type-bearing shapes are tried first, so nothing changes for them."""
    cu = _channel_update_bytes(scid=21)
    stream = varint_encode(len(cu) - 2) + cu + _typeless(_channel_update_bytes(scid=22))
    msgs = list(iter_snapshot_messages(_write(tmp_path, stream)))
    assert sorted(m.scid for m in msgs if isinstance(m, ChannelUpdate)) == [21, 22]
