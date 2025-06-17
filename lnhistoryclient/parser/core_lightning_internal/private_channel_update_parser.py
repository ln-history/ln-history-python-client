import io
import struct
from typing import Union

from lnhistoryclient.model.core_lightning_internal.PrivateChannelUpdate import PrivateChannelUpdate


def parse(data: Union[bytes, io.BytesIO]) -> PrivateChannelUpdate:
    """
    Parses a byte stream into a PrivateChannelUpdate object.

    This function reads a 2-byte length field followed by that many bytes
    of channel update data for a private channel.

    Args:
        data (Union[bytes, io.BytesIO]): Raw binary data or stream representing a private channel update.

    Returns:
        PrivateChannelUpdate: Parsed private channel update message.
    """
    stream = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    length_bytes = stream.read(2)
    if len(length_bytes) != 2:
        raise ValueError("Failed to read 2-byte length prefix")

    length = struct.unpack(">H", length_bytes)[0]
    update = stream.read(length)
    if len(update) != length:
        raise ValueError(f"Expected {length} bytes, got {len(update)}")

    return PrivateChannelUpdate(update=update)
