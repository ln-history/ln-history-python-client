import io
import struct
from typing import Any, Dict, Union

from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement
from lnhistoryclient.model.platform_internal.PlatformEvent import PlatformEvent
from lnhistoryclient.model.platform_internal.PlatformEventMetadata import PlatformEventMetadata
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
    )


def parse_platform_event(data: Dict[str, Any]) -> PlatformEvent:
    """
    Validates and parses a dictionary into a PlatformEvent dataclass.

    This function ensures that the input dictionary has the correct structure and types
    expected for a PlatformEvent, which includes:
      - A 'metadata' dict with fields:
          - 'type': int
          - 'id': hex string
          - 'timestamp': int
      - A 'raw_gossip_hex' field: hex string

    Args:
        data (Dict[str, Any]): The untrusted dictionary to validate and parse.

    Returns:
        PlatformEvent: A fully validated and parsed event object.

    Raises:
        ValueError: If the structure or types are incorrect.
    """

    if not isinstance(data, dict):
        raise ValueError("PlatformEvent must be a dictionary")

    if "metadata" not in data or not isinstance(data["metadata"], dict):
        raise ValueError("PlatformEvent must contain a 'metadata' dictionary")

    meta = data["metadata"]
    missing_meta_keys = [k for k in ("type", "id", "timestamp") if k not in meta]
    if missing_meta_keys:
        raise ValueError(f"Missing keys in metadata: {missing_meta_keys}")

    # --- Validate metadata.type ---
    if not isinstance(meta["type"], int):
        raise ValueError(f"'metadata.type' must be an integer, got {type(meta['type'])}")

    # --- Validate metadata.id ---
    id_value = meta["id"]
    if not isinstance(id_value, str):
        raise ValueError(f"'metadata.id' must be a hex string, got {type(id_value)}")

    if len(id_value) != 64:
        raise ValueError("metadata.id must be exactly 64 hex characters (32 bytes)")

    try:
        _ = bytes.fromhex(id_value)
    except ValueError as ve:
        raise ValueError(f"'metadata.id' {id_value} is not valid hex: {ve}") from ve

    # --- Validate timestamp ---
    if not isinstance(meta["timestamp"], int):
        raise ValueError(f"'timestamp' must be an integer, got {type(meta['timestamp'])}")

    metadata = PlatformEventMetadata(type=meta["type"], id=id_value, timestamp=meta["timestamp"])

    # --- Validate raw_gossip_hex ---
    raw_gossip_value = data.get("raw_gossip_hex")
    if not isinstance(raw_gossip_value, str):
        raise ValueError(f"'raw_gossip_hex' must be a hex string, got {type(raw_gossip_value)}")

    try:
        _ = bytes.fromhex(raw_gossip_value)
    except ValueError as ve:
        raise ValueError(f"'raw_gossip_hex' {raw_gossip_value} is not valid hex: {ve}") from ve

    return PlatformEvent(metadata=metadata, raw_gossip_hex=raw_gossip_value)
