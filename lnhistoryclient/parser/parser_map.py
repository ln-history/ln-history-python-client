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
from lnhistoryclient.parser import (
    channel_announcement_parser,
    channel_update_parser,
    node_announcement_parser,
)
from lnhistoryclient.parser.core_lightning_internal import (
    channel_amount_parser,
    channel_dying_parser,
    delete_channel_parser,
    gossip_store_ended_parser,
    private_channel_announcement_parser,
    private_channel_update_parser,
)

# Map message type integers to their corresponding parser function
PARSER_MAP: Dict[int, Callable] = {
    256: channel_announcement_parser.parse,
    257: node_announcement_parser.parse,
    258: channel_update_parser.parse,
    4101: channel_amount_parser.parse,
    4102: private_channel_announcement_parser.parse,
    4103: private_channel_update_parser.parse,
    4104: delete_channel_parser.parse,
    4105: gossip_store_ended_parser.parse,
    4106: channel_dying_parser.parse,
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
