# mypy: ignore-errors

import io
from typing import IO, Dict, Protocol, Union

from lnhistoryclient.model import (
    ChannelAnnouncement,
    ChannelUpdate,
    NodeAnnouncement,
)
from lnhistoryclient.model.core_lightning_internal import (
    ChannelAmount,
    ChannelDying,
    DeleteChannel,
    GossipStoreEnded,
    PrivateChannelAnnouncement,
    PrivateChannelUpdate,
)
from lnhistoryclient.parser import (
    channel_announcement_parser,
    channel_update_parser,
    node_announcement_parser,
)
from lnhistoryclient.parser.common import read_exact
from lnhistoryclient.parser.core_lightning_internal import (
    channel_amount_parser,
    channel_dying_parser,
    delete_channel_parser,
    gossip_store_ended_parser,
    private_channel_announcement_parser,
    private_channel_update_parser,
)

# Union of all possible parsed message types
ParsedMessage = Union[
    ChannelAnnouncement.ChannelAnnouncement,
    NodeAnnouncement.NodeAnnouncement,
    ChannelUpdate.ChannelUpdate,
    ChannelAmount.ChannelAmount,
    PrivateChannelAnnouncement.PrivateChannelAnnouncement,
    PrivateChannelUpdate.PrivateChannelUpdate,
    DeleteChannel.DeleteChannel,
    GossipStoreEnded.GossipStoreEnded,
    ChannelDying.ChannelDying,
]


class ParserFunction(Protocol):
    """Protocol for a parser function that parses binary data into a ParsedMessage."""

    def __call__(self, data: Union[bytes, IO[bytes]]) -> ParsedMessage: ...


# Map message type integers to their corresponding parser function
PARSER_MAP: Dict[int, ParserFunction] = {
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


def get_parser_by_message_type(message_type: int) -> ParserFunction:
    """Return the parser function for the given message type.

    Args:
        message_type (int): The type ID of the message.

    Returns:
        ParserFunction: A callable parser function.

    Raises:
        ValueError: If the message type is unknown.
    """
    try:
        return PARSER_MAP[message_type]
    except KeyError as e:
        raise ValueError(f"No parser found for message type {message_type}") from e


def get_parser_by_raw_hex(raw_hex: Union[bytes, IO[bytes]]) -> ParserFunction:
    """Determine the message type from raw binary and return the corresponding parser.

    Args:
        raw_hex (Union[bytes, IO[bytes]]): Raw binary message or a stream.

    Returns:
        ParserFunction: A callable parser function based on the message type.

    Raises:
        ValueError: If the message type cannot be determined.
    """
    stream = raw_hex if isinstance(raw_hex, io.BytesIO) else io.BytesIO(raw_hex)

    try:
        msg_type_bytes = read_exact(stream, 2)
        message_type = int.from_bytes(msg_type_bytes, byteorder="big")
        return get_parser_by_message_type(message_type)
    except Exception as e:
        raise ValueError(f"Failed to determine parser from raw hex: {e}") from e
