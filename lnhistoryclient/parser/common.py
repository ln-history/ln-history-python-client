import base64
import codecs
import io
import ipaddress
import struct
from typing import BinaryIO, Optional, Union

from lnhistoryclient.constants import CORE_LIGHTNING_TYPES, LIGHTNING_TYPES
from lnhistoryclient.model.Address import Address
from lnhistoryclient.model.AddressType import AddressType


def varint_decode(data: Union[bytes, io.BytesIO], big_endian: bool = False) -> Optional[int]:
    """
    Decodes a Bitcoin-style variable-length integer (varint) from a stream or bytes.

    Bitcoin's varint encoding:
    --------------------------
    A "varint" is used to encode unsigned integers efficiently by varying
    the number of bytes based on the value size. This is used throughout
    the Bitcoin protocol (for example, to encode the number of inputs
    and outputs in a transaction).

    The encoding rules are:
      - If value < 0xFD (253): encode as 1 byte (no prefix)
      - If value <= 0xFFFF (65535): prefix 0xFD + 2 bytes
      - If value <= 0xFFFFFFFF (4.29e9): prefix 0xFE + 4 bytes
      - If value <= 0xFFFFFFFFFFFFFFFF (~1.84e19): prefix 0xFF + 8 bytes

    Endianness in Bitcoin:
    ----------------------
    *Bitcoin always encodes the multi-byte portion in little-endian order.*
    This means:
      - 0xFD followed by 2 bytes: interpreted as little-endian 16-bit integer
      - 0xFE followed by 4 bytes: interpreted as little-endian 32-bit integer
      - 0xFF followed by 8 bytes: interpreted as little-endian 64-bit integer

    This matches the general rule that Bitcoin uses little-endian encoding
    for integers in protocol messages, block headers, and transaction data.
    (Only human-readable hex strings — like block hashes — are shown big-endian.)

    Parameters
    ----------
    data : Union[bytes, io.BytesIO]
        The input to decode. Can be a raw bytes object or an already-open
        BytesIO stream positioned at the varint start.
    big_endian : bool, default False
        If True, interprets multi-byte integers as big-endian instead of
        Bitcoin's default little-endian. This is primarily for testing
        or decoding non-standard data.

    Returns
    -------
    Optional[int]
        The decoded integer value, or None if the input is empty or invalid.

    Raises
    ------
    ValueError
        If there are not enough bytes to decode a complete varint.
        (Caught internally — function returns None instead.)

    Notes
    -----
    - For correct Bitcoin decoding, always use the default `big_endian=False`.
    - The prefix byte itself (0xFD, 0xFE, 0xFF) is a single byte, so endianness
      does not affect it.
    - This function will return None if decoding fails for any reason.
    """

    if isinstance(data, bytes):
        data = io.BytesIO(data)

    try:
        raw = read_exact(data, 1)
        if len(raw) != 1:
            return None

        prefix = raw[0]
        endian_char = ">" if big_endian else "<"

        if prefix < 0xFD:
            return prefix
        elif prefix == 0xFD:
            return int(struct.unpack(f"{endian_char}H", read_exact(data, 2))[0])
        elif prefix == 0xFE:
            return int(struct.unpack(f"{endian_char}L", read_exact(data, 4))[0])
        else:
            return int(struct.unpack(f"{endian_char}Q", read_exact(data, 8))[0])
    except Exception:
        return None


def varint_encode(value: int) -> bytes:
    """
    Encodes an integer into Bitcoin-style variable-length integer (varint).

    Encoding rules:
    - If value < 0xFD: encode directly as 1 byte.
    - If value <= 0xFFFF: prefix with 0xFD and append value as little-endian uint16.
    - If value <= 0xFFFFFFFF: prefix with 0xFE and append value as little-endian uint32.
    - Otherwise: prefix with 0xFF and append value as little-endian uint64.

    Args:
        value (int): The integer to encode.

    Returns:
        bytes: The Bitcoin varint encoding of the integer.

    Raises:
        ValueError: If the value is negative or too large to encode in uint64.
    """
    if value < 0:
        raise ValueError("varint cannot encode negative values")
    if value < 0xFD:
        return struct.pack("<B", value)
    elif value <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", value)
    elif value <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<L", value)
    elif value <= 0xFFFFFFFFFFFFFFFF:
        return b"\xff" + struct.pack("<Q", value)
    else:
        raise ValueError("varint too large (must fit in uint64)")


def get_message_type_by_bytes(raw_bytes: bytes) -> Optional[int]:
    """
    Extract the Lightning message type from the first two bytes.

    This checks whether the type is in the known message type sets.

    Args:
        raw_hex (bytes): Byte sequence that must be at least 2 bytes long.

    Returns:
        Optional[int]: The message type if recognized, otherwise None.

    Raises:
        ValueError: If raw_hex is less than 2 bytes.
    """
    if len(raw_bytes) < 2:
        raise ValueError("Insufficient data: Expected at least 2 bytes to extract message type.")

    msg_type: int = struct.unpack(">H", raw_bytes[:2])[0]
    if msg_type in LIGHTNING_TYPES or msg_type in CORE_LIGHTNING_TYPES:
        return msg_type
    return None


def to_base_32(addr: bytes) -> str:
    """
    Encodes a byte sequence using Base32 suitable for .onion addresses.

    This encoding:
    - Uses Base32 encoding without padding.
    - Converts the result to lowercase.

    Args:
        addr (bytes): The byte sequence to encode (e.g., 10 bytes for Tor v2, 35 bytes for Tor v3).

    Returns:
        str: Base32-encoded string suitable for a .onion address.
    """
    return base64.b32encode(addr).decode("ascii").strip("=").lower()


def parse_address(b: io.BytesIO) -> Optional[Address]:
    """
    Parses a binary-encoded address from a BytesIO stream.

    Supported address types:
    - Type 1: IPv4 (4 bytes + 2-byte port)
    - Type 2: IPv6 (16 bytes + 2-byte port)
    - Type 3: Tor v2 (10 bytes Base32 + 2-byte port)
    - Type 4: Tor v3 (35 bytes Base32 + 2-byte port)
    - Type 5: DNS hostname (1-byte length + hostname + 2-byte port)

    Rolls back the stream position and returns None if parsing fails.

    Args:
        b (io.BytesIO): A stream containing the binary address.

    Returns:
        Address | None: Parsed `Address` object or `None` if unknown type or error.
    """
    pos_before = b.tell()
    try:
        type_byte = read_exact(b, 1)
        type_id = struct.unpack("!B", type_byte)[0]

        a = Address()
        a.typ = AddressType(type_id)

        if type_id == 1:  # IPv4
            a.addr = str(ipaddress.IPv4Address(read_exact(b, 4)))
            (a.port,) = struct.unpack("!H", read_exact(b, 2))
        elif type_id == 2:  # IPv6
            raw = read_exact(b, 16)
            a.addr = f"[{ipaddress.IPv6Address(raw)}]"
            (a.port,) = struct.unpack("!H", read_exact(b, 2))
        elif type_id == 3:  # Tor v2
            raw = read_exact(b, 10)
            a.addr = to_base_32(raw) + ".onion"
            (a.port,) = struct.unpack("!H", read_exact(b, 2))
        elif type_id == 4:  # Tor v3
            raw = read_exact(b, 35)
            a.addr = to_base_32(raw) + ".onion"
            (a.port,) = struct.unpack("!H", read_exact(b, 2))
        elif type_id == 5:  # DNS
            hostname_len = struct.unpack("!B", read_exact(b, 1))[0]
            hostname = read_exact(b, hostname_len).decode("ascii")
            a.addr = hostname
            (a.port,) = struct.unpack("!H", read_exact(b, 2))
        else:
            return None

        return a
    except Exception as e:
        b.seek(pos_before)
        print(f"Error parsing address: {e}")
        return None


def read_exact(b: BinaryIO, n: int) -> bytes:
    """
    Reads exactly `n` bytes from a binary stream or raises an error.

    Args:
        b (BinaryIO): Input stream to read from (file, BytesIO, etc.).
        n (int): Number of bytes to read.

    Returns:
        bytes: The read bytes.

    Raises:
        ValueError: If fewer than `n` bytes could be read.
    """
    data = b.read(n)
    if len(data) != n:
        raise ValueError(f"Expected {n} bytes, got {len(data)}")
    return data


def decode_alias(alias_bytes: bytes) -> str:
    """
    Attempts to decode a node alias from a byte sequence.

    The function tries:
    1. UTF-8 decoding (common case).
    2. Punycode decoding if UTF-8 fails.
    3. Falls back to hexadecimal representation as a last resort.

    Null bytes are stripped from the result.

    Args:
        alias_bytes (bytes): Raw 32-byte alias from the node announcement.

    Returns:
        str: A human-readable string or hex-encoded fallback.
    """
    try:
        return alias_bytes.decode("utf-8").strip("\x00")
    except UnicodeDecodeError:
        try:
            cleaned = alias_bytes.strip(b"\x00")
            return codecs.decode(cleaned, "punycode")
        except Exception:
            return alias_bytes.hex()


def get_scid_from_int(scid_int: int) -> str:
    """
    Calculates the scid from integer to a human readable string:
    scid = blockheight x transactionIndex x outputId

    For more information see the specification BOLT #7:
    https://github.com/lightning/bolts/blob/master/07-routing-gossip.md#definition-of-short_channel_id

    Args:
        scid_int: Scid in integer representation

    Returns:
        str: Formatted string representing the scid.
    """

    block = (scid_int >> 40) & 0xFFFFFF
    txindex = (scid_int >> 16) & 0xFFFFFF
    output = scid_int & 0xFFFF
    return f"{block}x{txindex}x{output}"


def strip_known_message_type(data: bytes) -> bytes:
    """
    Strips a known 2-byte message type prefix from the beginning of a Lightning message.

    This function checks whether the input starts with any known gossip or Core Lightning
    message type (defined in `constants.py`). If a match is found, the 2-byte prefix is removed.

    Args:
        data (bytes): Raw binary message data, possibly including a 2-byte type prefix.

    Returns:
        bytes: The message content with the type prefix removed if recognized,
               otherwise the original input.

    Raises:
        ValueError: If the input is too short or not valid binary data.
    """
    try:
        if len(data) < 2:
            raise ValueError("Input data is too short to contain a message type prefix.")

        known_types = LIGHTNING_TYPES | CORE_LIGHTNING_TYPES
        prefix = int.from_bytes(data[:2], byteorder="big")

        if prefix in known_types:
            return data[2:]

        return data
    except Exception as e:
        raise ValueError(f"Failed to strip known message type: {e}") from e
