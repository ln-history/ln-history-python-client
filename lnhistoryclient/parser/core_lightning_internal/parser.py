import io
import struct
from typing import Union

from lnhistoryclient.model.core_lightning_internal.ChannelAmount import ChannelAmount
from lnhistoryclient.model.core_lightning_internal.ChannelDying import ChannelDying
from lnhistoryclient.model.core_lightning_internal.DeleteChannel import DeleteChannel
from lnhistoryclient.model.core_lightning_internal.GossipStoreEnded import GossipStoreEnded
from lnhistoryclient.model.core_lightning_internal.PrivateChannelAnnouncement import PrivateChannelAnnouncement
from lnhistoryclient.model.core_lightning_internal.PrivateChannelUpdate import PrivateChannelUpdate


def parse_channel_amount(data: Union[bytes, io.BytesIO]) -> ChannelAmount:
    """
    Parses a byte stream into a ChannelAmount object.

    This function deserializes an 8-byte unsigned integer representing
    the amount in satoshis for a channel.

    Args:
        data (bytes): Raw binary data representing the channel amount.

    Returns:
        ChannelAmount: Parsed channel amount object.
    """
    b = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    satoshis_bytes = b.read(8)
    if len(satoshis_bytes) != 8:
        raise ValueError("Expected 8 bytes for satoshis")
    satoshis = struct.unpack(">Q", satoshis_bytes)[0]

    return ChannelAmount(satoshis=satoshis)


def parse_channel_dying(data: Union[bytes, io.BytesIO]) -> ChannelDying:
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


def parse_delete_channel(data: Union[bytes, io.BytesIO]) -> DeleteChannel:
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


def parse_gossip_store_ended(data: Union[bytes, io.BytesIO]) -> GossipStoreEnded:
    """
    Parses a byte stream into a GossipStoreEnded object.

    This function reads the equivalent offset (8 bytes) marking the end
    of a gossip store file segment.

    Args:
        data (bytes): Raw binary data representing the end-of-store marker.

    Returns:
        GossipStoreEnded: Parsed end-of-store message.
    """
    stream = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    offset_bytes = stream.read(8)
    if len(offset_bytes) != 8:
        raise ValueError("Expected 8 bytes for equivalent offset")
    equivalent_offset = struct.unpack(">Q", offset_bytes)[0]

    return GossipStoreEnded(equivalent_offset=equivalent_offset)


def parse_private_channel_announcement(data: Union[bytes, io.BytesIO]) -> PrivateChannelAnnouncement:
    """
    Parses a byte stream into a PrivateChannelUpdate object.

    This function reads a 2-byte length field followed by that many bytes
    of channel announcement data for a private channel.

    Args:
        data (Union[bytes, io.BytesIO]): Raw binary data or stream representing a private channel update.

    Returns:
        PrivateChannelAnnouncement: Parsed private channel announcement message.
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


def parse_private_channel_update(data: Union[bytes, io.BytesIO]) -> PrivateChannelUpdate:
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
