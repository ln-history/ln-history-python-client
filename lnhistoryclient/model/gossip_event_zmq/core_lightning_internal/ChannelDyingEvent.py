from typing import TypedDict

from lnhistoryclient.model.core_lightning_internal.types import ChannelDyingDict
from lnhistoryclient.model.MessageMetadata import MessageMetadata


class GossipMessage(TypedDict):
    metadata: MessageMetadata
    raw_hex: str
    parsed: ChannelDyingDict
