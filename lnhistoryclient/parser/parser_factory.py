import io
from typing import Callable, Dict, Union, TypeVar
from common import read_exact

from lnhistoryclient.parser import channel_announcement_parser
from lnhistoryclient.parser import channel_update_parser
from lnhistoryclient.parser import node_announcement_parser

from lnhistoryclient.parser.core_lightning_internal import (
    channel_amount_parser,
    private_channel_announcement_parser,
    private_channel_update_parser,
    delete_channel_parser,
    gossip_store_ended_parser,
    channel_dying_parser,
)

# Define a generic type for parsed message objects
ParsedMessage = TypeVar("ParsedMessage")

# Mapping of message type to corresponding parser function
PARSER_MAP: Dict[int, Callable[[bytes], ParsedMessage]] = {
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


def get_parser_by_message_type(message_type: int) -> Callable[[bytes], ParsedMessage]:
    """
    Returns the parser function for the given gossip message type.

    Args:
        message_type (int): The integer message type identifier.

    Returns:
        Callable[[bytes], ParsedMessage]: The parser function.

    Raises:
        ValueError: If no parser is found for the given message type.
    """
    try:
        return PARSER_MAP[message_type]
    except KeyError:
        raise ValueError(f"No parser found for message type: {message_type}")


def get_parser_by_raw_hex(raw_hex: Union[bytes, io.BytesIO]) -> Callable[[bytes], ParsedMessage]:
    """
    Extracts the message type from the raw hex input and returns the corresponding parser function.

    Args:
        raw_hex (Union[bytes, io.BytesIO]): Raw gossip message as bytes or a BytesIO stream.

    Returns:
        Callable[[bytes], ParsedMessage]: A parser function for the message type.

    Raises:
        ValueError: If the message type could not be determined or is unsupported.
    """
    stream = raw_hex if isinstance(raw_hex, io.BytesIO) else io.BytesIO(raw_hex)

    try:
        msg_type_bytes = read_exact(stream, 2)
        message_type = int.from_bytes(msg_type_bytes, byteorder="big")
        return get_parser_by_message_type(message_type)
    except Exception as e:
        raise ValueError(f"Failed to determine parser from raw hex: {e}") from e
