from typing import TypedDict


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
