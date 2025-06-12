import io
import struct
from lnhistoryclient.model.core_lightning_internal.ChannelAmount import ChannelAmount 

def parse(data: bytes) -> ChannelAmount:
    """
    Parses a byte stream into a ChannelAmount object.

    This function deserializes an 8-byte unsigned integer representing
    the amount in satoshis for a channel.

    Args:
        data (bytes): Raw binary data representing the channel amount.

    Returns:
        ChannelAmount: Parsed channel amount object.
    """

    b = io.BytesIO(data)
    satoshis = struct.unpack(">Q", b.read(8))[0]
    return ChannelAmount(satoshis=satoshis)