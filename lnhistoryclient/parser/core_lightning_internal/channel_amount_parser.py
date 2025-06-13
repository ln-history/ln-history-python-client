import io
import struct
from typing import Union
from lnhistoryclient.model.core_lightning_internal.ChannelAmount import ChannelAmount

def parse(data: Union[bytes, io.BytesIO]) -> ChannelAmount:
    """
    Parses a byte stream into a ChannelAmount object.

    This function deserializes an 8-byte unsigned integer representing
    the amount in satoshis for a channel.

    Args:
        data (bytes): Raw binary data representing the channel amount.

    Returns:
        ChannelAmount: Parsed channel amount object.
    """
    stream = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    satoshis_bytes = stream.read(8)
    if len(satoshis_bytes) != 8:
        raise ValueError("Expected 8 bytes for satoshis")
    satoshis = struct.unpack(">Q", satoshis_bytes)[0]

    return ChannelAmount(satoshis=satoshis)
