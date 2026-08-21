"""Deterministic factories for synthetic gossip bytes — shared test infrastructure.

Every component of the ln-history pipeline (this library, gossip-publisher-zmq,
gossip-processor) needs the same fixtures: structurally valid gossip messages,
envelope framings (canonical and the historical drifted shapes), and CLN
gossip_store files. Before this module existed each test suite hand-built them
with ``struct.pack`` — four divergent copies at last count.

Design notes:
- Plain functions returning ``bytes`` — no builder classes to maintain.
- All output is deterministic (seeded), so tests can assert exact hashes.
- Messages are *structurally* valid (parseable, plausible), not cryptographically
  valid: signatures are filler and keys are not real curve points.
- Only :func:`make_store_record` needs the ``crc32c`` package (the ``testing``
  extra); everything else is stdlib-only, keeping the parser core dependency-free.

Example::

    from lnhistoryclient.testing import make_channel_update, canonical_envelope
    raw = canonical_envelope(make_channel_update(scid=123, fee_base_msat=1500))
"""

import hashlib
import struct
from typing import Iterable, Optional, Tuple, Union

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
from lnhistoryclient.framing import build_raw_gossip
from lnhistoryclient.parser.common import varint_encode

# Bitcoin mainnet genesis hash in wire order (as it appears inside gossip messages;
# parsers reverse it for display).
CHAIN_HASH_MAINNET = bytes.fromhex("6fe28c0ab6f1b372c1a6a246ae63f74f931e8365e15a089c68d6190000000000")

DEFAULT_TIMESTAMP = 1_700_000_000  # 2023-11-14, comfortably "plausible"


# --------------------------------------------------------------------- primitives
def make_pubkey(seed: int = 1) -> bytes:
    """33-byte compressed-pubkey-shaped bytes (0x02/0x03 prefix), deterministic."""
    prefix = bytes([0x02 + (seed % 2)])
    return prefix + hashlib.sha256(f"lnhistory-pubkey-{seed}".encode()).digest()


def make_signature(seed: int = 1) -> bytes:
    """64-byte signature-shaped filler, deterministic."""
    h = hashlib.sha256(f"lnhistory-signature-{seed}".encode()).digest()
    return h + h


def make_uuid(seed: int = 1) -> bytes:
    """32-byte store-generation uuid, deterministic."""
    return hashlib.sha256(f"lnhistory-store-uuid-{seed}".encode()).digest()


# ------------------------------------------------------------- BOLT #7 messages
# All builders return the TYPE-PREFIXED message (uint16_be type ++ payload).


def make_channel_announcement(
    scid: int = 700_000_000_000_000_001,
    node1_seed: int = 1,
    node2_seed: int = 2,
    features: bytes = b"",
    chain_hash: bytes = CHAIN_HASH_MAINNET,
) -> bytes:
    msg = struct.pack(">H", MSG_TYPE_CHANNEL_ANNOUNCEMENT)
    for sig_seed in (node1_seed, node2_seed, node1_seed + 100, node2_seed + 100):
        msg += make_signature(sig_seed)
    msg += struct.pack(">H", len(features)) + features
    msg += chain_hash
    msg += struct.pack(">Q", scid)
    msg += make_pubkey(node1_seed) + make_pubkey(node2_seed)
    msg += make_pubkey(node1_seed + 200) + make_pubkey(node2_seed + 200)
    return msg


def make_node_announcement(
    node_seed: int = 1,
    timestamp: int = DEFAULT_TIMESTAMP,
    alias: bytes = b"ln-history-test-node",
    rgb_color: bytes = b"\xaa\xbb\xcc",
    features: bytes = b"",
    addresses: bytes = b"",
) -> bytes:
    if len(alias) > 32:
        raise ValueError("alias must be at most 32 bytes")
    msg = struct.pack(">H", MSG_TYPE_NODE_ANNOUNCEMENT)
    msg += make_signature(node_seed)
    msg += struct.pack(">H", len(features)) + features
    msg += struct.pack(">I", timestamp)
    msg += make_pubkey(node_seed)
    msg += rgb_color
    msg += alias.ljust(32, b"\x00")
    msg += struct.pack(">H", len(addresses)) + addresses
    return msg


def make_channel_update(
    scid: int = 700_000_000_000_000_001,
    direction: int = 0,
    timestamp: int = DEFAULT_TIMESTAMP,
    cltv_expiry_delta: int = 40,
    htlc_minimum_msat: int = 1_000,
    fee_base_msat: int = 1_000,
    fee_proportional_millionths: int = 10,
    htlc_maximum_msat: Optional[int] = 16_000_000_000,
    disabled: bool = False,
    chain_hash: bytes = CHAIN_HASH_MAINNET,
    sig_seed: int = 1,
    tlv_tail: bytes = b"",
) -> bytes:
    """``htlc_maximum_msat=None`` builds the 128-byte-payload legacy layout;
    ``tlv_tail`` appends raw TLV bytes (e.g. an LND inbound-fee extension)."""
    message_flags = 1 if htlc_maximum_msat is not None else 0
    channel_flags = (direction & 1) | (2 if disabled else 0)
    msg = struct.pack(">H", MSG_TYPE_CHANNEL_UPDATE)
    msg += make_signature(sig_seed)
    msg += chain_hash
    msg += struct.pack(">Q", scid)
    msg += struct.pack(">I", timestamp)
    msg += bytes([message_flags, channel_flags])
    msg += struct.pack(">H", cltv_expiry_delta)
    msg += struct.pack(">Q", htlc_minimum_msat)
    msg += struct.pack(">I", fee_base_msat)
    msg += struct.pack(">I", fee_proportional_millionths)
    if htlc_maximum_msat is not None:
        msg += struct.pack(">Q", htlc_maximum_msat)
    return msg + tlv_tail


# ------------------------------------------------- Core Lightning internal types
def make_channel_amount(satoshis: int = 1_000_000) -> bytes:
    return struct.pack(">H", MSG_TYPE_CHANNEL_AMOUNT) + struct.pack(">Q", satoshis)


def make_delete_channel(scid: int = 700_000_000_000_000_001) -> bytes:
    return struct.pack(">H", MSG_TYPE_DELETE_CHANNEL) + struct.pack(">Q", scid)


def make_channel_dying(scid: int = 700_000_000_000_000_001, blockheight: int = 800_000) -> bytes:
    return struct.pack(">H", MSG_TYPE_CHANNEL_DYING) + struct.pack(">Q", scid) + struct.pack(">I", blockheight)


def make_private_channel_update(inner: Optional[bytes] = None) -> bytes:
    """4102: len-prefixed wrapped channel_update."""
    inner = inner if inner is not None else make_channel_update()
    return struct.pack(">H", MSG_TYPE_PRIVATE_CHANNEL_UPDATE) + struct.pack(">H", len(inner)) + inner


def make_private_channel_announcement(amount_sat: int = 1_000_000, inner: Optional[bytes] = None) -> bytes:
    """4104: amount + len-prefixed wrapped channel_announcement."""
    inner = inner if inner is not None else make_channel_announcement()
    return (
        struct.pack(">H", MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT)
        + struct.pack(">Q", amount_sat)
        + struct.pack(">H", len(inner))
        + inner
    )


def make_gossip_store_ended(equivalent_offset: int = 1, uuid: Optional[bytes] = None) -> bytes:
    """4105. ``uuid=None`` builds the pre-v26.06 8-byte layout; a 32-byte uuid
    builds the v26.06 (gossip_store v16) 40-byte layout."""
    msg = struct.pack(">H", MSG_TYPE_GOSSIP_STORE_ENDED) + struct.pack(">Q", equivalent_offset)
    if uuid is not None:
        if len(uuid) != 32:
            raise ValueError("uuid must be 32 bytes")
        msg += uuid
    return msg


def make_gossip_store_uuid(uuid: Optional[bytes] = None) -> bytes:
    """4107 (CLN >= v26.06): the store-generation record at the front of a store."""
    uuid = uuid if uuid is not None else make_uuid()
    if len(uuid) != 32:
        raise ValueError("uuid must be 32 bytes")
    return struct.pack(">H", MSG_TYPE_GOSSIP_STORE_UUID) + uuid


# ---------------------------------------------------------------- envelope shapes
# canonical_envelope is the production framing; the drifted shapes reproduce the
# historical conventions found in the wild by the 2026-08 raw_gossip audit, for
# regression-testing readers and repair tooling. Never write drifted shapes to
# anything but tests.

canonical_envelope = build_raw_gossip


def offby2_envelope(msg_with_type: bytes) -> bytes:
    """Varint wrongly counts payload PLUS the 2-byte type (the plugin's pre-2026
    framing; 42% of drifted channel_updates)."""
    return varint_encode(len(msg_with_type)) + msg_with_type


def no_varint_envelope(msg_with_type: bytes) -> bytes:
    """No length prefix at all — the blob starts with the 2-byte type."""
    return msg_with_type


def varint_payload_envelope(msg_with_type: bytes) -> bytes:
    """Varint + payload with the 2-byte type STRIPPED (49.5% of drifted
    channel_updates; invisible to type-scanning readers)."""
    payload = msg_with_type[2:]
    return varint_encode(len(payload)) + payload


def payload_only_envelope(msg_with_type: bytes) -> bytes:
    """Bare payload: no varint, no type."""
    return msg_with_type[2:]


# ------------------------------------------------------------ gossip_store files
GOSSIP_STORE_VERSION_V15 = (0 << 5) | 15  # CLN v25.12
GOSSIP_STORE_VERSION_V16 = (0 << 5) | 16  # CLN v26.06

_RecordSpec = Union[bytes, Tuple[bytes, int]]


def make_store_record(msg_with_type: bytes, flags: int = 0, timestamp: int = DEFAULT_TIMESTAMP) -> bytes:
    """One gossip_store record: header (flags, len, crc, timestamp) + message.

    Requires the ``crc32c`` package (``pip install lnhistoryclient[testing]``).
    """
    try:
        from crc32c import crc32c
    except ImportError as exc:  # pragma: no cover
        raise ImportError("make_store_record needs the crc32c package: pip install 'lnhistoryclient[testing]'") from exc
    crc = crc32c(msg_with_type, timestamp) & 0xFFFFFFFF
    return struct.pack(">HHII", flags, len(msg_with_type), crc, timestamp) + msg_with_type


def make_gossip_store(
    messages: Iterable[_RecordSpec] = (),
    version: int = GOSSIP_STORE_VERSION_V16,
    store_uuid: Optional[bytes] = None,
    timestamp: int = DEFAULT_TIMESTAMP,
) -> bytes:
    """A complete gossip_store file: version byte, optional 4107 uuid record
    (pass ``store_uuid`` to mimic CLN >= v26.06), then one record per message.
    Each item is either ``bytes`` or ``(bytes, flags)``.
    """
    out = bytes([version])
    if store_uuid is not None:
        out += make_store_record(make_gossip_store_uuid(store_uuid), timestamp=timestamp)
    for item in messages:
        msg, flags = item if isinstance(item, tuple) else (item, 0)
        out += make_store_record(msg, flags=flags, timestamp=timestamp)
    return out
