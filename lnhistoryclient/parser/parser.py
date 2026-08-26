import io
import struct
from typing import Optional, Tuple, Union

from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement
from lnhistoryclient.parser.common import read_exact


def parse_channel_announcement(data: Union[bytes, io.BytesIO]) -> ChannelAnnouncement:
    """
    Parses a byte stream or BytesIO into a ChannelAnnouncement object.

    This function deserializes a `channel_announcement` message from the Lightning Network gossip protocol.
    It extracts all required digital signatures, keys, feature bits, and metadata to reconstruct the full
    announcement used to signal a new channel.

    Args:
        data (Union[bytes, io.BytesIO]): Raw binary data or BytesIO representing a channel announcement message.

    Returns:
        ChannelAnnouncement: Parsed channel announcement with signatures, keys, and identifiers.
    """

    b = io.BytesIO(data) if isinstance(data, bytes) else data

    node_signature_1 = b.read(64)
    node_signature_2 = b.read(64)
    bitcoin_signature_1 = b.read(64)
    bitcoin_signature_2 = b.read(64)
    features_len = struct.unpack(">H", b.read(2))[0]
    features = b.read(features_len)
    chain_hash = b.read(32)[::-1]
    scid = struct.unpack(">Q", b.read(8))[0]
    node_id_1 = b.read(33)
    node_id_2 = b.read(33)
    bitcoin_key_1 = b.read(33)
    bitcoin_key_2 = b.read(33)

    return ChannelAnnouncement(
        features=features,
        chain_hash=chain_hash,
        scid=scid,
        node_id_1=node_id_1,
        node_id_2=node_id_2,
        bitcoin_key_1=bitcoin_key_1,
        bitcoin_key_2=bitcoin_key_2,
        node_signature_1=node_signature_1,
        node_signature_2=node_signature_2,
        bitcoin_signature_1=bitcoin_signature_1,
        bitcoin_signature_2=bitcoin_signature_2,
    )


def parse_node_announcement(data: Union[bytes, io.BytesIO]) -> NodeAnnouncement:
    """
    Parses a byte stream or BytesIO into a NodeAnnouncement object.

    This function deserializes a `node_announcement` message from the Lightning Network gossip protocol.
    It extracts signature, identity, visual representation, and associated address data for a network node.

    Args:
        data (Union[bytes, io.BytesIO]): Raw binary data or BytesIO representing a node announcement message.

    Returns:
        NodeAnnouncement: Parsed node identity with visual alias and address information.
    """

    b = io.BytesIO(data) if isinstance(data, bytes) else data

    signature = read_exact(b, 64)
    features_len = struct.unpack("!H", read_exact(b, 2))[0]
    features = b.read(features_len)

    timestamp = struct.unpack("!I", read_exact(b, 4))[0]
    node_id = read_exact(b, 33)
    rgb_color = read_exact(b, 3)
    alias = read_exact(b, 32)

    address_len = struct.unpack("!H", read_exact(b, 2))[0]
    address_bytes_data = read_exact(b, address_len)

    return NodeAnnouncement(
        signature=signature,
        features=features,
        timestamp=timestamp,
        node_id=node_id,
        rgb_color=rgb_color,
        alias=alias,
        addresses=address_bytes_data,
    )


#: BigSize type 55555 (0xd903) followed by length 8 — the exact byte prefix of lnd's
#: inbound-fee TLV record. Guarding on the literal bytes rather than running a general
#: TLV walk keeps the hot path cheap and makes a malformed tail yield ``None`` rather
#: than a plausible-looking wrong number. The same guard is used by the ``lnhistory``
#: database's generated columns, so the two decoders agree by construction.
_INBOUND_FEE_TLV_PREFIX = b"\xfd\xd9\x03\x08"


def parse_inbound_fees(tail: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Decode lnd's inbound-fee TLV (record type 55555) from a channel_update's tail.

    Returns ``(base_msat, proportional_millionths)``, or ``(None, None)`` when the tail
    does not carry the record.

    Both values are **signed** int32 (two's complement on the wire). Negative means a
    *discount* for routing into the channel, which is the common case; lnd gates setting
    positive values behind ``--accept-positive-inbound-fees``, but positive values do
    occur on the wire.

    ``None`` and ``0`` are different facts and both are preserved: ``None`` means the
    message carries no TLV at all, ``0`` means the record is present and explicitly zero
    (lnd >= 0.18 with no inbound fee configured). The distinction doubles as an
    implementation fingerprint, so callers should not collapse it.
    """
    if len(tail) < 12 or not tail.startswith(_INBOUND_FEE_TLV_PREFIX):
        return None, None
    base = int.from_bytes(tail[4:8], byteorder="big", signed=True)
    rate = int.from_bytes(tail[8:12], byteorder="big", signed=True)
    return base, rate


def parse_channel_update(data: Union[bytes, io.BytesIO]) -> ChannelUpdate:
    """
    Parses a byte stream or BytesIO into a ChannelUpdate object.

    This function deserializes a `channel_update` message from the Lightning Network gossip protocol.
    It extracts the routing policy and metadata including fee structures, direction flags,
    and optional maximum HTLC value.

    Args:
        data (Union[bytes, io.BytesIO]): Raw binary data or BytesIO representing a channel update message.

    Returns:
        ChannelUpdate: Parsed update containing routing policy parameters and channel state.
    """

    b = io.BytesIO(data) if isinstance(data, bytes) else data

    signature = b.read(64)
    chain_hash = b.read(32)[::-1]
    scid = struct.unpack(">Q", b.read(8))[0]
    timestamp = struct.unpack(">I", b.read(4))[0]
    message_flags = b.read(1)
    channel_flags = b.read(1)
    cltv_expiry_delta = struct.unpack(">H", b.read(2))[0]
    htlc_minimum_msat = struct.unpack(">Q", b.read(8))[0]
    fee_base_msat = struct.unpack(">I", b.read(4))[0]
    fee_proportional_millionths = struct.unpack(">I", b.read(4))[0]

    htlc_maximum_msat = None
    if message_flags[0] & 1:
        htlc_maximum_msat = struct.unpack(">Q", b.read(8))[0]

    # Anything after the standard fields is the TLV extension stream. Only read it when
    # the caller handed us a bounded ``bytes`` for exactly one message: given a shared
    # BytesIO we cannot know where this message ends, and consuming the tail would eat
    # the next message. ``parse_channel_update_extended`` covers the stream case.
    inbound_fee_base_msat: Optional[int] = None
    inbound_fee_proportional_millionths: Optional[int] = None
    if isinstance(data, bytes):
        inbound_fee_base_msat, inbound_fee_proportional_millionths = parse_inbound_fees(data[b.tell() :])

    return ChannelUpdate(
        signature=signature,
        chain_hash=chain_hash,
        scid=scid,
        timestamp=timestamp,
        message_flags=message_flags,
        channel_flags=channel_flags,
        cltv_expiry_delta=cltv_expiry_delta,
        htlc_minimum_msat=htlc_minimum_msat,
        fee_base_msat=fee_base_msat,
        fee_proportional_millionths=fee_proportional_millionths,
        htlc_maximum_msat=htlc_maximum_msat,
        inbound_fee_base_msat=inbound_fee_base_msat,
        inbound_fee_proportional_millionths=inbound_fee_proportional_millionths,
    )
