import io
import struct
from typing import Union
from model.NodeAnnouncement import NodeAnnouncement
from parser.common import read_exact

def parse(data: Union[bytes, io.BytesIO]) -> NodeAnnouncement:
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
        addresses=address_bytes_data
    )
