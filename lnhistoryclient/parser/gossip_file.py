import logging
import struct
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from lnhistoryclient.constants import HEADER_FORMAT
from lnhistoryclient.parser.common import varint_decode

HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def read_gossip_file(path_to_file: str, start: int = 0, logger: Optional[logging.Logger] = None) -> Iterator[bytes]:
    """
    Open a gossip data file (GSP, Core Lightning gossip_store, or plain varint)
    and yield messages. Automatically detects format.

    Args:
        path_to_file (str): Path to the gossip data file.
        start (int): Number of messages to skip before yielding.
        logger (Optional[Logger]): Logger for debug/info output. If None, uses a default Logger.

    Yields:
        bytes: Individual gossip messages read from the file.
    """
    logger = logger or logging.Logger("default")
    path = Path(path_to_file)

    if not path.exists():
        logger.error(f"Gossip file does not exist: {path_to_file}")
        return

    def read_exact(f: BinaryIO, n: int) -> bytes:
        data = f.read(n)
        if len(data) != n:
            raise ValueError("Unexpected end of file")
        return data

    skipped = 0
    yielded = 0

    with open(path, "rb") as f:
        # Peek first 4 bytes for format detection
        header = f.read(4)
        f.seek(0)  # rewind

        # --- Format detection ---
        if len(header) == 4 and header[:3] == b"GSP" and header[3] == 1:
            fmt = "GSP"
            logger.info("Detected GSP dataset format")
        else:
            # Try gossip_store version byte
            version_byte = f.read(1)
            f.seek(0)
            if version_byte:
                major_version = (version_byte[0] >> 5) & 0x07
                if major_version == 0:
                    fmt = "gossip_store"
                    logger.info("Detected Core Lightning gossip_store format")
                else:
                    fmt = "plain"
                    logger.info("Assuming plain varint-delimited gossip file format")
            else:
                fmt = "plain"
                logger.info("Assuming plain varint-delimited gossip file format")

        # --- Universal reading loop ---
        while True:
            try:
                if fmt == "GSP":
                    # 4-byte header already read, skip it
                    if f.tell() == 0:
                        _ = f.read(4)
                    length = varint_decode(f, big_endian=True)
                    if not length:
                        break
                    msg = read_exact(f, length)

                elif fmt == "gossip_store":
                    record_header = f.read(HEADER_SIZE)
                    if len(record_header) < HEADER_SIZE:
                        break
                    _ = int.from_bytes(record_header[0:2], "big")  # flags
                    length = int.from_bytes(record_header[2:4], "big")
                    _ = int.from_bytes(record_header[4:8], "big")  # crc
                    _ = int.from_bytes(record_header[8:12], "big")  # timestamp
                    msg = read_exact(f, length)

                else:  # plain varint format
                    length = varint_decode(f)
                    if not length:
                        break
                    msg = read_exact(f, length)

                if skipped < start:
                    skipped += 1
                    continue

                yield msg
                yielded += 1

                if yielded % 100_000 == 0:
                    logger.debug(f"Yielded {yielded} messages (start offset: {start})")

            except Exception as e:
                logger.error(f"Error reading gossip file: {e}")
                break
