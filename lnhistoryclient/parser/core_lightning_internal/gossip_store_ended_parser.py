import io
import struct
from typing import Union

from lnhistoryclient.model.core_lightning_internal.GossipStoreEnded import GossipStoreEnded


def parse(data: Union[bytes, io.BytesIO]) -> GossipStoreEnded:
    """
    Parses a byte stream into a GossipStoreEnded object.

    This function reads the equivalent offset (8 bytes) marking the end
    of a gossip store file segment.

    Args:
        data (bytes): Raw binary data representing the end-of-store marker.

    Returns:
        GossipStoreEnded: Parsed end-of-store message.
    """
    stream = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    offset_bytes = stream.read(8)
    if len(offset_bytes) != 8:
        raise ValueError("Expected 8 bytes for equivalent offset")
    equivalent_offset = struct.unpack(">Q", offset_bytes)[0]

    return GossipStoreEnded(equivalent_offset=equivalent_offset)
