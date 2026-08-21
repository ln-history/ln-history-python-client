"""Canonical ``raw_gossip`` envelope — the single source of truth for framing.

Every gossip blob stored or transported by the ln-history pipeline uses ONE format::

    raw_gossip = varint_le(len(payload)) ++ uint16_be(msg_type) ++ payload
    gossip_id  = sha256(raw_gossip).hexdigest()          # lowercase, 64 chars

The varint is a Bitcoin CompactSize (little-endian) and counts the payload
**excluding** the 2-byte type.

Historical note: this definition used to live only inside gossip-processor
(``_build_raw_gossip``), while other components framed independently. The
divergent reimplementations produced the envelope drift (off-by-two varints,
missing type bytes, ...) that required the 2026-08 database repair of ~148M rows.
Import these functions instead of re-deriving the format.
"""

import hashlib

from lnhistoryclient.parser.common import varint_encode

__all__ = [
    "build_raw_gossip",
    "build_raw_gossip_from_parts",
    "strip_varint_prefix",
    "compute_gossip_id",
    "canonical_gossip_id",
]


def build_raw_gossip(msg_with_type: bytes) -> bytes:
    """Frame a type-prefixed gossip message (``uint16_be type ++ payload``) into
    the canonical envelope. This is the exact algorithm of gossip-processor's
    ``_build_raw_gossip``."""
    if len(msg_with_type) < 2:
        raise ValueError("message must contain at least the 2-byte type")
    return varint_encode(len(msg_with_type) - 2) + msg_with_type


def build_raw_gossip_from_parts(msg_type: int, payload: bytes) -> bytes:
    """Frame a message given its type and bare payload (without the type bytes)."""
    return varint_encode(len(payload)) + msg_type.to_bytes(2, "big") + payload


def strip_varint_prefix(data: bytes) -> bytes:
    """Remove a leading CompactSize varint, returning the bytes after it
    (which start with the 2-byte message type for a canonical envelope).

    The varint's *value* is deliberately ignored — historical blobs carry
    varints under several conventions, and ingest must tolerate all of them
    before re-framing canonically. Returns ``b""`` if the buffer is shorter
    than its varint prefix claims to be.
    """
    if not data:
        return data
    first = data[0]
    if first < 0xFD:
        return data[1:]
    size = {0xFD: 3, 0xFE: 5, 0xFF: 9}[first]
    if len(data) < size:
        return b""
    return data[size:]


def compute_gossip_id(raw_gossip: bytes) -> str:
    """The primary key of a gossip message: sha256 over its canonical envelope."""
    return hashlib.sha256(raw_gossip).hexdigest()


def canonical_gossip_id(msg_with_type: bytes) -> str:
    """Convenience: gossip_id of a type-prefixed message after canonical framing."""
    return compute_gossip_id(build_raw_gossip(msg_with_type))
