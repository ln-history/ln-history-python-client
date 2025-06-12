import io
import struct
from lnhistoryclient.model.core_lightning_internal.DeleteChannel import DeleteChannel

def parse(data: bytes) -> DeleteChannel:
    """
    Parses a byte stream into a DeleteChannel object.

    This function deserializes an 8-byte short_channel_id indicating 
    the deletion of a previously announced channel.

    Args:
        data (bytes): Raw binary data representing a delete channel message.

    Returns:
        DeleteChannel: Parsed delete channel object.
    """
    
    b = io.BytesIO(data)
    short_channel_id = struct.unpack(">Q", b.read(8))[0]
    return DeleteChannel(short_channel_id=short_channel_id)