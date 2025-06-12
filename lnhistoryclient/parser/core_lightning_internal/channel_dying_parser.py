import io
import struct
from lnhistoryclient.model.core_lightning_internal.ChannelDying import ChannelDying

def parse(data: bytes) -> ChannelDying:
    """
    Parses a byte stream into a ChannelDying object.

    This function deserializes a message that indicates a channel is 
    about to be closed. It extracts the short_channel_id and the 
    blockheight at which the channel is expected to die.

    Args:
        data (bytes): Raw binary data representing a dying channel.

    Returns:
        ChannelDying: Parsed object containing SCID and blockheight.
    """

    b = io.BytesIO(data)
    short_channel_id = struct.unpack(">Q", b.read(8))[0]
    blockheight = struct.unpack(">I", b.read(4))[0]
    return ChannelDying(short_channel_id=short_channel_id, blockheight=blockheight)