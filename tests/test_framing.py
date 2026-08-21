"""Tests for lnhistoryclient.framing — the canonical raw_gossip envelope.

The exact byte conventions here are load-bearing: gossip-processor writes this
format into the database (gossip_id = sha256(envelope) is the primary key), and
the 2026-08 repair normalised ~148M rows to it. A change that slips past these
tests would silently fork the database's identity scheme.
"""

import hashlib
import struct

from lnhistoryclient.framing import (
    build_raw_gossip,
    build_raw_gossip_from_parts,
    canonical_gossip_id,
    compute_gossip_id,
    strip_varint_prefix,
)
from lnhistoryclient.testing import (
    make_channel_announcement,
    make_channel_update,
    no_varint_envelope,
    offby2_envelope,
    payload_only_envelope,
    varint_payload_envelope,
)


def test_canonical_envelope_matches_historical_example():
    """The memory-anchored vector: a 438-byte channel_announcement payload frames
    as fd b6 01 (438 little-endian) ++ 01 00 ++ payload."""
    payload = b"\xab" * 438
    env = build_raw_gossip_from_parts(256, payload)
    assert env[:3] == b"\xfd\xb6\x01"  # CompactSize 438, little-endian
    assert env[3:5] == b"\x01\x00"  # type 256 big-endian
    assert env[5:] == payload


def test_varint_excludes_the_type_bytes():
    msg = make_channel_update(htlc_maximum_msat=None)  # 130 bytes incl. type
    env = build_raw_gossip(msg)
    assert env[0] == len(msg) - 2 == 128  # single-byte varint counts payload only


def test_build_from_parts_equals_build_from_message():
    msg = make_channel_announcement()
    assert build_raw_gossip(msg) == build_raw_gossip_from_parts(256, msg[2:])


def test_strip_varint_prefix_inverts_build():
    for msg in (make_channel_update(), make_channel_announcement(), b"\x01\x00" + b"x" * 500):
        assert strip_varint_prefix(build_raw_gossip(msg)) == msg


def test_strip_varint_prefix_tolerates_all_widths():
    body = b"\x01\x02payload"
    assert strip_varint_prefix(b"\x05" + body) == body
    assert strip_varint_prefix(b"\xfd\x00\x01" + body) == body
    assert strip_varint_prefix(b"\xfe\x00\x00\x00\x01" + body) == body
    assert strip_varint_prefix(b"\xff" + b"\x00" * 8 + body) == body
    assert strip_varint_prefix(b"\xfd\x00") == b""  # truncated varint
    assert strip_varint_prefix(b"") == b""


def test_gossip_id_is_sha256_of_envelope():
    msg = make_channel_update(scid=42)
    env = build_raw_gossip(msg)
    assert compute_gossip_id(env) == hashlib.sha256(env).hexdigest()
    assert canonical_gossip_id(msg) == compute_gossip_id(env)
    assert len(canonical_gossip_id(msg)) == 64


def test_drifted_envelope_shapes_reproduce_the_audit_taxonomy():
    """The four drifted shapes found in the wild, distinct from canonical and
    from each other — reader/repair regression fixtures depend on these."""
    msg = make_channel_update(htlc_maximum_msat=None)
    payload = msg[2:]
    canonical = build_raw_gossip(msg)

    offby2 = offby2_envelope(msg)
    assert offby2[0] == len(msg)  # counts type + payload: the plugin's old bug
    assert offby2 != canonical

    assert no_varint_envelope(msg) == msg

    vp = varint_payload_envelope(msg)
    assert vp[0] == len(payload) and vp[1:] == payload  # type bytes gone

    assert payload_only_envelope(msg) == payload

    envelopes = {canonical, offby2, no_varint_envelope(msg), vp, payload_only_envelope(msg)}
    assert len(envelopes) == 5


def test_offby2_recanonicalises_through_strip_and_rebuild():
    """The ingest path that healed drifted blobs: strip whatever varint is there,
    then rebuild canonically. Must be idempotent for canonical input."""
    msg = make_channel_update(scid=99)
    for envelope in (build_raw_gossip(msg), offby2_envelope(msg)):
        assert build_raw_gossip(strip_varint_prefix(envelope)) == build_raw_gossip(msg)


def test_multibyte_varint_is_little_endian():
    payload = b"z" * 300
    env = build_raw_gossip_from_parts(258, payload)
    assert env[0] == 0xFD
    assert struct.unpack("<H", env[1:3])[0] == 300
