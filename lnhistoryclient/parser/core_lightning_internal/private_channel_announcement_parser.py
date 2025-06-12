import io
import struct
from lnhistoryclient.model.core_lightning_internal.PrivateChannelAnnouncement import PrivateChannelAnnouncement

def parse(data: bytes) -> PrivateChannelAnnouncement:
    """
    Parses a byte stream into a PrivateChannelAnnouncement object.

    This function reads the amount in satoshis and a variable-length 
    byte buffer representing the private channel's announcement data.

    Args:
        data (bytes): Raw binary data representing a private channel announcement.

    Returns:
        PrivateChannelAnnouncement: Parsed private channel announcement.
    """

    b = io.BytesIO(data)
    amount_sat = struct.unpack(">Q", b.read(8))[0]
    length = struct.unpack(">H", b.read(2))[0]
    announcement = b.read(length)
    return PrivateChannelAnnouncement(amount_sat=amount_sat, announcement=announcement)
