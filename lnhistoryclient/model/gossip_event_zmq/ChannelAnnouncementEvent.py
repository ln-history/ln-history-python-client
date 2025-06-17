from typing import TypedDict

from lnhistoryclient.model.MessageMetadata import MessageMetadata
from lnhistoryclient.model.types import ChannelAnnouncementDict


class GossipMessage(TypedDict):
    metadata: MessageMetadata
    raw_hex: str
    parsed: ChannelAnnouncementDict
