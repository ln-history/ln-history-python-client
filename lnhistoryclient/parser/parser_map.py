# type: ignore

from typing import Callable, Dict

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
