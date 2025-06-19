from typing import TypedDict, Union

from lnhistoryclient.model.types import MessageMetadata


class ChannelAmountDict(TypedDict):
    satoshis: int


class ChannelDyingDict(TypedDict):
    scid: str
    blockheight: int


class DeleteChannelDict(TypedDict):
    scid: str


class GossipStoreEndedDict(TypedDict):
    equivalent_offset: int


class PrivateChannelAnnouncementDict(TypedDict):
    amount_sat: int
    announcement: str


class PrivateChannelUpdateDict(TypedDict):
    update: str


ParsedCoreLightningGossipDict = Union[
    ChannelAmountDict,
    ChannelDyingDict,
    DeleteChannelDict,
    GossipStoreEndedDict,
    PrivateChannelAnnouncementDict,
    PrivateChannelUpdateDict,
]


class CoreLightningGossipPayload(TypedDict):
    metadata: MessageMetadata
    raw_hex: str
    parsed: ParsedCoreLightningGossipDict
