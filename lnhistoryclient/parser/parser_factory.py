# mypy: ignore-errors

import io
from typing import Callable, Optional, Union

from lnhistoryclient.model.core_lightning_internal.types import ParsedCoreLightningGossipDict
from lnhistoryclient.model.types import ParsedGossipDict
from lnhistoryclient.parser.common import get_message_type_by_bytes
from lnhistoryclient.parser.parser_map import PARSER_MAP


def parse_gossip_msg(raw: bytes) -> Optional[Union[ParsedGossipDict, ParsedCoreLightningGossipDict]]:
    """
    Parse a raw gossip message into a dataclass.

    Args:
        raw (bytes): Raw gossip message.

    Returns:
        Optional[Union[ParsedGossipDict, ParsedCoreLightningGossipDict]]: Parsed model or None if not parseable.
    """
    parser = get_parser_from_bytes(raw)
    if parser is None:
        return None

    try:
        return parser(raw)
    except Exception:
        return None


def get_parser_by_message_type(message_type: int) -> Callable:
    """
    Return the parser for a given Lightning message type.

    Args:
        message_type (int): Type ID.

    Returns:
        Callable: The parser function.

    Raises:
        ValueError: If the message type is unknown.
    """
    try:
        return PARSER_MAP[message_type]
    except KeyError as e:
        raise ValueError(f"No parser found for message type {message_type}") from e


def get_parser_from_bytes(raw_hex: Union[bytes, io.BytesIO]) -> Optional[Callable]:
    """
    Convenience method to get parser directly from raw message bytes.

    WARNING: This assumes the first two bytes represent a valid Lightning gossip type.
    Use only if you are confident the data is valid.

    Args:
        raw_hex (Union[bytes, IO[bytes]]): Message data including the 2-byte type prefix.

    Returns:
        Optional[Callable]: The corresponding parser if known, otherwise None.
    """
    if isinstance(raw_hex, io.BytesIO):
        peek = raw_hex.read(2)
        raw_hex.seek(0)
    else:
        peek = raw_hex[:2]

    try:
        msg_type = get_message_type_by_bytes(peek)
        if msg_type is None:
            return None
        return get_parser_by_message_type(msg_type)
    except Exception:
        return None
