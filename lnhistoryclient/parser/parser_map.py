# type: ignore

from typing import Callable, Dict, Type, TypedDict

from lnhistoryclient.constants import (
    MSG_TYPE_CHANNEL_AMOUNT,
    MSG_TYPE_CHANNEL_ANNOUNCEMENT,
    MSG_TYPE_CHANNEL_DYING,
    MSG_TYPE_CHANNEL_UPDATE,
    MSG_TYPE_DELETE_CHANNEL,
    MSG_TYPE_GOSSIP_STORE_ENDED,
    MSG_TYPE_NODE_ANNOUNCEMENT,
    MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT,
    MSG_TYPE_PRIVATE_CHANNEL_UPDATE,
)
from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncementDict
from lnhistoryclient.model.ChannelUpdate import ChannelUpdateDict
from lnhistoryclient.model.core_lightning_internal.ChannelAmount import ChannelAmountDict
from lnhistoryclient.model.core_lightning_internal.ChannelDying import ChannelDyingDict
from lnhistoryclient.model.core_lightning_internal.DeleteChannel import DeleteChannelDict
from lnhistoryclient.model.core_lightning_internal.GossipStoreEnded import GossipStoreEndedDict
from lnhistoryclient.model.core_lightning_internal.PrivateChannelAnnouncement import PrivateChannelAnnouncementDict
from lnhistoryclient.model.core_lightning_internal.PrivateChannelUpdate import PrivateChannelUpdateDict
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncementDict
from lnhistoryclient.parser.core_lightning_internal.parser import (
    parse_channel_amount,
    parse_channel_dying,
    parse_delete_channel,
    parse_gossip_store_ended,
    parse_private_channel_announcement,
    parse_private_channel_update,
)
from lnhistoryclient.parser.parser import (
    parse_channel_announcement,
    parse_channel_update,
    parse_node_announcement,
)

# Map message type integers to their corresponding parser function
PARSER_MAP: Dict[int, Callable] = {
    256: parse_channel_announcement,
    257: parse_node_announcement,
    258: parse_channel_update,
    4101: parse_channel_amount,
    4102: parse_private_channel_announcement,
    4103: parse_private_channel_update,
    4104: parse_delete_channel,
    4105: parse_gossip_store_ended,
    4106: parse_channel_dying,
}

# Map gossip message types to their expected parsed dict type
GOSSIP_TYPE_TO_PARSED_TYPE: dict[int, Type[TypedDict]] = {
    MSG_TYPE_CHANNEL_ANNOUNCEMENT: ChannelAnnouncementDict,
    MSG_TYPE_NODE_ANNOUNCEMENT: NodeAnnouncementDict,
    MSG_TYPE_CHANNEL_UPDATE: ChannelUpdateDict,
    MSG_TYPE_CHANNEL_AMOUNT: ChannelAmountDict,
    MSG_TYPE_DELETE_CHANNEL: DeleteChannelDict,
    MSG_TYPE_CHANNEL_DYING: ChannelDyingDict,
    MSG_TYPE_GOSSIP_STORE_ENDED: GossipStoreEndedDict,
    MSG_TYPE_PRIVATE_CHANNEL_UPDATE: PrivateChannelUpdateDict,
    MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT: PrivateChannelAnnouncementDict,
}
