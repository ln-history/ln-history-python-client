import io
import struct
from typing import Union
from lnhistoryclient.model.core_lightning_internal.ChannelDying import ChannelDying

def parse(data: Union[bytes, io.BytesIO]) -> ChannelDying:
    """
    Parses a byte stream into a ChannelDying object.

    This function deserializes a message that indicates a channel is 
    about to be closed. It extracts the scid and the 
    blockheight at which the channel is expected to die.

    Args:
        data (bytes): Raw binary data representing a dying channel.

    Returns:
        ChannelDying: Parsed object containing SCID and blockheight.
    """
    b = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    scid_bytes = b.read(8)
    if len(scid_bytes) != 8:
        raise ValueError("Expected 8 bytes for scid")
    scid = struct.unpack(">Q", scid_bytes)[0]

    blockheight_bytes = b.read(4)
    if len(blockheight_bytes) != 4:
        raise ValueError("Expected 4 bytes for blockheight")
    blockheight = struct.unpack(">I", blockheight_bytes)[0]

    return ChannelDying(scid=scid, blockheight=blockheight)
