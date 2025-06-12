import io
import struct
from lnhistoryclient.model.core_lightning_internal.PrivateChannelUpdate import PrivateChannelUpdate

def parse(data: bytes) -> PrivateChannelUpdate:
    """
    Parses a byte stream into a PrivateChannelUpdate object.

    This function reads a 2-byte length field followed by that many bytes 
    of channel update data for a private channel.

    Args:
        data (bytes): Raw binary data representing a private channel update.

    Returns:
        PrivateChannelUpdate: Parsed private channel update message.
    """

    b = io.BytesIO(data)
    length = struct.unpack(">H", b.read(2))[0]
    update = b.read(length)
    return PrivateChannelUpdate(update=update)