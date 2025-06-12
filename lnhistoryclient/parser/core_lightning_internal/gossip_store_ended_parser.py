import io
import struct
from lnhistoryclient.model.core_lightning_internal.GossipStoreEnded import GossipStoreEnded

def parse(data: bytes) -> GossipStoreEnded:
    """
    Parses a byte stream into a GossipStoreEnded object.

    This function reads the equivalent offset (8 bytes) marking the end 
    of a gossip store file segment.

    Args:
        data (bytes): Raw binary data representing the end-of-store marker.

    Returns:
        GossipStoreEnded: Parsed end-of-store message.
    """
    
    b = io.BytesIO(data)
    equivalent_offset = struct.unpack(">Q", b.read(8))[0]
    return GossipStoreEnded(equivalent_offset=equivalent_offset)