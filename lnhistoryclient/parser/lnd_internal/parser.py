import io
from typing import Union

from lnhistoryclient.model.lnd_internal.ExtendedChannelUpdate import ExtendedChannelUpdate
from lnhistoryclient.model.lnd_internal.LNDExtension import LNDExtension
from lnhistoryclient.parser.parser import parse_channel_update


def parse_channel_update_extended(data: Union[bytes, io.BytesIO]) -> ExtendedChannelUpdate:
    """
    Parses a channel_update and checks for trailing bytes containing LND extensions.

    Args:
        data: The payload bytes (typically with the 2-byte message type STRIPPED).
    """
    stream = io.BytesIO(data) if isinstance(data, bytes) else data

    # This consumes the standard bytes (Signature -> HTLC Max)
    # The stream cursor will be left exactly where the standard data ends.
    core_update = parse_channel_update(stream)

    # If there are bytes remaining in the stream, they are extensions.
    current_pos = stream.tell()
    stream.seek(0, io.SEEK_END)
    end_pos = stream.tell()

    extension_data = None

    if current_pos < end_pos:
        # Rewind to where the standard parser left off
        stream.seek(current_pos)
        # Parse the tail
        extension_data = LNDExtension.from_stream(stream, byte_order="<")

    return ExtendedChannelUpdate(core=core_update, extension=extension_data)
