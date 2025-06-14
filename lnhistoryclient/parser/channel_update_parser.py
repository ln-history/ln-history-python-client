import io
import struct
from typing import Union
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate

def parse(data: Union[bytes, io.BytesIO]) -> ChannelUpdate:
    """
    Parses a byte stream or BytesIO into a ChannelUpdate object.

    This function deserializes a `channel_update` message from the Lightning Network gossip protocol.
    It extracts the routing policy and metadata including fee structures, direction flags, 
    and optional maximum HTLC value.

    Args:
        data (Union[bytes, io.BytesIO]): Raw binary data or BytesIO representing a channel update message.

    Returns:
        ChannelUpdate: Parsed update containing routing policy parameters and channel state.
    """
    
    b = io.BytesIO(data) if isinstance(data, bytes) else data

    signature = b.read(64)
    chain_hash = b.read(32)[::-1]
    short_channel_id = struct.unpack(">Q", b.read(8))[0]
    timestamp = struct.unpack(">I", b.read(4))[0]
    message_flags = b.read(1)
    channel_flags = b.read(1)
    cltv_expiry_delta = struct.unpack(">H", b.read(2))[0]
    htlc_minimum_msat = struct.unpack(">Q", b.read(8))[0]
    fee_base_msat = struct.unpack(">I", b.read(4))[0]
    fee_proportional_millionths = struct.unpack(">I", b.read(4))[0]

    htlc_maximum_msat = None
    if message_flags[0] & 1:
        htlc_maximum_msat = struct.unpack(">Q", b.read(8))[0]

    return ChannelUpdate(
        signature=signature,
        chain_hash=chain_hash,
        scid=short_channel_id,
        timestamp=timestamp,
        message_flags=message_flags,
        channel_flags=channel_flags,
        cltv_expiry_delta=cltv_expiry_delta,
        htlc_minimum_msat=htlc_minimum_msat,
        fee_base_msat=fee_base_msat,
        fee_proportional_millionths=fee_proportional_millionths,
        htlc_maximum_msat=htlc_maximum_msat
    )
