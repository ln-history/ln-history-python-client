from typing import TypedDict


class MessageMetadata(TypedDict):
    type: int
    timestamp: int
    sender_node_id: str
    length: str  # Length in bytes without starting 2-byte type
