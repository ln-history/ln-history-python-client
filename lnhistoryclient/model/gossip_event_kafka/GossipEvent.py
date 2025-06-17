from typing import TypedDict


class MessageMetadata(TypedDict):
    type: int
    id: str
    timestamp: int


class GossipMessage(TypedDict):
    metadata: MessageMetadata
    raw_hex: str
