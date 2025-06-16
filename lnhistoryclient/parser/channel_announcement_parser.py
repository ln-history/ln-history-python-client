import io
import struct
from typing import Union
from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement

def parse(data: Union[bytes, io.BytesIO]) -> ChannelAnnouncement:
    """
    Parses a byte stream or BytesIO into a ChannelAnnouncement object.

    This function deserializes a `channel_announcement` message from the Lightning Network gossip protocol.
    It extracts all required digital signatures, keys, feature bits, and metadata to reconstruct the full 
    announcement used to signal a new channel.

    Args:
        data (Union[bytes, io.BytesIO]): Raw binary data or BytesIO representing a channel announcement message.

    Returns:
        ChannelAnnouncement: Parsed channel announcement with signatures, keys, and identifiers.
    """
    
    b = io.BytesIO(data) if isinstance(data, bytes) else data

    node_signature_1 = b.read(64)
    node_signature_2 = b.read(64)
    bitcoin_signature_1 = b.read(64)
    bitcoin_signature_2 = b.read(64)
    features_len = struct.unpack(">H", b.read(2))[0]
    features = b.read(features_len)
    chain_hash = b.read(32)[::-1]
    scid = struct.unpack(">Q", b.read(8))[0]
    node_id_1 = b.read(33)
    node_id_2 = b.read(33)
    bitcoin_key_1 = b.read(33)
    bitcoin_key_2 = b.read(33)

    return ChannelAnnouncement(
        features=features,
        chain_hash=chain_hash,
        scid=scid,
        node_id_1=node_id_1,
        node_id_2=node_id_2,
        bitcoin_key_1=bitcoin_key_1,
        bitcoin_key_2=bitcoin_key_2,
        node_signature_1=node_signature_1,
        node_signature_2=node_signature_2,
        bitcoin_signature_1=bitcoin_signature_1,
        bitcoin_signature_2=bitcoin_signature_2
    )
