import io
import struct
from typing import Union

from lnhistoryclient.model.core_lightning_internal.DeleteChannel import DeleteChannel


def parse(data: Union[bytes, io.BytesIO]) -> DeleteChannel:
    """
    Parses a byte stream into a DeleteChannel object.

    This function deserializes an 8-byte scid indicating
    the deletion of a previously announced channel.

    Args:
        data (bytes): Raw binary data representing a delete channel message.

    Returns:
        DeleteChannel: Parsed delete channel object.
    """
    stream = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    scid_bytes = stream.read(8)
    if len(scid_bytes) != 8:
        raise ValueError("Expected 8 bytes for scid")
    scid = struct.unpack(">Q", scid_bytes)[0]

    return DeleteChannel(scid=scid)
