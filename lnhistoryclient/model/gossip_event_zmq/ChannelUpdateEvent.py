from typing import TypedDict

from lnhistoryclient.model.MessageMetadata import MessageMetadata
from lnhistoryclient.model.types import ChannelUpdateDict


class GossipMessage(TypedDict):
    metadata: MessageMetadata
    raw_hex: str
    parsed: ChannelUpdateDict
