from typing import TypedDict, Union

from lnhistoryclient.model.types import BasePluginEvent


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


# ---------------------------------------------------------------------
# PluginEvent refers to all events published by the gossip-publisher-zmq Core Lightning plugin


class PluginPrivateChannelUpdateEvent(BasePluginEvent):
    parsed: PrivateChannelUpdateDict


class PluginPrivateChannelAnnouncementEvent(BasePluginEvent):
    parsed: PrivateChannelAnnouncementDict


class PluginGossipStoreEndedEvent(BasePluginEvent):
    parsed: GossipStoreEndedDict


class PluginDeleteChannelEvent(BasePluginEvent):
    parsed: DeleteChannelDict


class PluginChannelDyingEvent(BasePluginEvent):
    parsed: ChannelDyingDict


class PluginChannelAmountEvent(BasePluginEvent):
    parsed: ChannelAmountDict


ParsedCoreLightningGossipDict = Union[
    ChannelAmountDict,
    ChannelDyingDict,
    DeleteChannelDict,
    GossipStoreEndedDict,
    PrivateChannelAnnouncementDict,
    PrivateChannelUpdateDict,
]


class PluginCoreLightningEvent(BasePluginEvent):
    parsed: ParsedCoreLightningGossipDict
