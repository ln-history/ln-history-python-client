from typing import List, Optional, TypedDict, Union


class AddressTypeDict(TypedDict):
    id: int
    name: str


class AddressDict(TypedDict):
    typ: AddressTypeDict
    addr: str
    port: int


class NodeAnnouncementDict(TypedDict):
    signature: str
    features: str
    timestamp: int
    node_id: str
    rgb_color: str
    alias: str
    addresses: List[AddressDict]


class ChannelAnnouncementDict(TypedDict):
    features: str
    chain_hash: str
    scid: str
    node_id_1: str
    node_id_2: str
    bitcoin_key_1: str
    bitcoin_key_2: str
    node_signature_1: str
    node_signature_2: str
    bitcoin_signature_1: str
    bitcoin_signature_2: str


class ChannelUpdateDict(TypedDict):
    signature: str
    chain_hash: str
    scid: str
    timestamp: int
    message_flags: str
    channel_flags: str
    cltv_expiry_delta: int
    htlc_minimum_msat: int
    fee_base_msat: int
    fee_proportional_millionths: int
    htlc_maximum_msat: Optional[int]


class MessageMetadata(TypedDict):
    type: int
    timestamp: int
    sender_node_id: str
    length: str  # Length in bytes without starting 2-byte type


ParsedGossipDict = Union[
    ChannelAnnouncementDict,
    NodeAnnouncementDict,
    ChannelUpdateDict,
]


class GossipPayload(TypedDict):
    metadata: MessageMetadata
    raw_hex: str
    parsed: ParsedGossipDict
