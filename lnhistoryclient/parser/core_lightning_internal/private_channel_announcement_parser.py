import io
import struct
from typing import Union
from lnhistoryclient.model.core_lightning_internal.PrivateChannelAnnouncement import PrivateChannelAnnouncement

def parse(data: Union[bytes, io.BytesIO]) -> PrivateChannelAnnouncement:
    """
    Parses a byte stream into a PrivateChannelAnnouncement object.

    This function reads the amount in satoshis and a variable-length 
    byte buffer representing the private channel's announcement data.
    """
    stream = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    amount_bytes = stream.read(8)
    if len(amount_bytes) != 8:
        raise ValueError("Expected 8 bytes for amount_sat")
    amount_sat = struct.unpack(">Q", amount_bytes)[0]

    length_bytes = stream.read(2)
    if len(length_bytes) != 2:
        raise ValueError("Expected 2 bytes for length prefix")
    length = struct.unpack(">H", length_bytes)[0]

    announcement = stream.read(length)
    if len(announcement) != length:
        raise ValueError(f"Expected {length} bytes for announcement, got {len(announcement)}")

    return PrivateChannelAnnouncement(amount_sat=amount_sat, announcement=announcement)
