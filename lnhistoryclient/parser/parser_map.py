# type: ignore

from typing import Callable, Dict, Type, TypedDict

from lnhistoryclient.constants import (
    MSG_TYPE_CHANNEL_AMOUNT,
    MSG_TYPE_CHANNEL_ANNOUNCEMENT,
    MSG_TYPE_CHANNEL_DYING,
    MSG_TYPE_CHANNEL_UPDATE,
    MSG_TYPE_DELETE_CHANNEL,
    MSG_TYPE_GOSSIP_STORE_ENDED,
    MSG_TYPE_GOSSIP_STORE_UUID,
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
from lnhistoryclient.model.core_lightning_internal.GossipStoreUuid import GossipStoreUuidDict
from lnhistoryclient.model.core_lightning_internal.PrivateChannelAnnouncement import PrivateChannelAnnouncementDict
from lnhistoryclient.model.core_lightning_internal.PrivateChannelUpdate import PrivateChannelUpdateDict
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncementDict
from lnhistoryclient.parser.core_lightning_internal.parser import (
    parse_channel_amount,
    parse_channel_dying,
    parse_delete_channel,
    parse_gossip_store_ended,
    parse_gossip_store_uuid,
    parse_private_channel_announcement,
    parse_private_channel_update,
)
from lnhistoryclient.parser.parser import (
    parse_channel_announcement,
    parse_channel_update,
    parse_node_announcement,
)

# Map message type integers to their corresponding parser function.
# Keyed on the named constants deliberately: a previous revision used raw ints
# and silently rotated 4102/4103/4104 (private_update -> announcement parser,
# delete_channel -> update parser, private_announcement -> delete parser) —
# which is why the ZMQ plugin historically special-cased delete_channel.
PARSER_MAP: Dict[int, Callable] = {
    MSG_TYPE_CHANNEL_ANNOUNCEMENT: parse_channel_announcement,
    MSG_TYPE_NODE_ANNOUNCEMENT: parse_node_announcement,
    MSG_TYPE_CHANNEL_UPDATE: parse_channel_update,
    MSG_TYPE_CHANNEL_AMOUNT: parse_channel_amount,
    MSG_TYPE_PRIVATE_CHANNEL_UPDATE: parse_private_channel_update,
    MSG_TYPE_DELETE_CHANNEL: parse_delete_channel,
    MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT: parse_private_channel_announcement,
    MSG_TYPE_GOSSIP_STORE_ENDED: parse_gossip_store_ended,
    MSG_TYPE_CHANNEL_DYING: parse_channel_dying,
    MSG_TYPE_GOSSIP_STORE_UUID: parse_gossip_store_uuid,
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
    MSG_TYPE_GOSSIP_STORE_UUID: GossipStoreUuidDict,
    MSG_TYPE_PRIVATE_CHANNEL_UPDATE: PrivateChannelUpdateDict,
    MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT: PrivateChannelAnnouncementDict,
}
