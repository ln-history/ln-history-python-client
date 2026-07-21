import logging
import struct
from pathlib import Path
from typing import BinaryIO, Iterator

from lnhistoryclient.constants import GOSSIP_STORE_DELETED_BIT, HEADER_FORMAT
from lnhistoryclient.parser.common import varint_decode

# Constants
GOSSIP_STORE_HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
GSP_HEADER = b"GSP\x01"


def _read_exact(f: BinaryIO, n: int) -> bytes:
    """Helper to read exactly n bytes or raise an error if EOF is reached prematurely."""
    data = f.read(n)
    if len(data) != n:
        raise ValueError(f"Unexpected end of file. Expected {n} bytes, got {len(data)}.")
    return data


def _iter_gsp(f: BinaryIO) -> Iterator[bytes]:
    """Yields messages from a GSP file (skips 4-byte header)."""
    f.seek(4)
    while True:
        # Assuming varint_decode is available in your scope from lnhistoryclient or similar
        length = varint_decode(f, big_endian=True)
        if not length:
            break
        yield _read_exact(f, length)


def _iter_gossip_store(f: BinaryIO) -> Iterator[bytes]:
    """Yields messages from a Core Lightning gossip_store file (skips version byte).

    Header layout per record (all big-endian):
      flags(2) | length(2) | crc(4) | timestamp(4)

    Flag bits of interest:
      0x8000  GOSSIP_STORE_DELETED_BIT  — record is logically deleted, data is zeroed; skip it
      0x4000  GOSSIP_STORE_PRIVATE_BIT  — private channel gossip; yielded as-is
      0x0800  GOSSIP_STORE_PUSH_BIT     — push to peers annotation; data is valid, yielded as-is
    """
    f.seek(1)
    while True:
        header = f.read(GOSSIP_STORE_HEADER_SIZE)
        if len(header) < GOSSIP_STORE_HEADER_SIZE:
            break

        flags = int.from_bytes(header[0:2], "big")
        length = int.from_bytes(header[2:4], "big")
        data = _read_exact(f, length)

        if flags & GOSSIP_STORE_DELETED_BIT:
            continue

        yield data


def _iter_plain(f: BinaryIO) -> Iterator[bytes]:
    """Yields messages from a plain varint-delimited file."""
    f.seek(0)
    while True:
        length = varint_decode(f, big_endian=True)
        if not length:
            break
        yield _read_exact(f, length)


def read_gossip_file(path_to_file: str, start: int = 0) -> Iterator[bytes]:
    """
    Open a gossip data file and yield messages. Automatically detect the correct format:
      + Core Lightning gossip_store (see https://docs.corelightning.org/docs/contribute-to-core-lightning#gossip_store-direct-access-to-lightning-gossip for details)
      + GSP (custom format by Christian Decker used here https://github.com/lnresearch/topology)
      + or plain bytes (using bigEndian for varint length encoding before each message for length information)

    Args:
        path_to_file (str): Path to the gossip data file.
        start (int): Number of messages to skip before yielding.
        end (int): Number of messages to yield before skipping.

    Yields:
        bytes: Individual gossip messages read from the file.
    """
    logger = logging.getLogger("default")
    path = Path(path_to_file)

    if not path.exists():
        logger.error(f"Gossip file does not exist: {path_to_file}")
        return

    with open(path, "rb") as f:
        # --- Format Detection ---
        header_peek = f.read(4)
        f.seek(0)

        message_iterator: Iterator[bytes]

        if header_peek == GSP_HEADER:
            logger.info("Detected GSP dataset format")
            message_iterator = _iter_gsp(f)

        else:
            version_byte = f.read(1)
            f.seek(0)

            is_gossip_store = False
            if version_byte:
                major_version = (version_byte[0] >> 5) & 0x07  # this must be 4 bits major, 4 bits minor
                if major_version == 0:
                    is_gossip_store = True

            if is_gossip_store:
                logger.info("Detected Core Lightning gossip_store format")
                message_iterator = _iter_gossip_store(f)
            else:
                logger.info("Assuming plain varint-delimited gossip file format")
                message_iterator = _iter_plain(f)

        try:
            yielded_count = 0

            for i, msg in enumerate(message_iterator):
                if i < start:
                    continue

                yield msg
                yielded_count += 1

        except Exception as e:
            logger.error(f"Error reading gossip file: {e}")
