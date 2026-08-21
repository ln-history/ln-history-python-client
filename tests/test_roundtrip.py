"""Build -> parse round-trips for every message type, via the real dispatch path.

These tests route through PARSER_MAP / parser_factory exactly as consumers do —
which is the point: a build->parse mismatch ANYWHERE (builder drift, parser drift,
or a wrong map entry) fails here. The 4102/4103/4104 tests are regression tests
for a real bug: PARSER_MAP once keyed on raw ints and rotated those three types,
so delete_channel bytes hit the private-update parser (the ZMQ plugin worked
around it with a special case instead of anyone noticing the map).
"""

import pytest

from lnhistoryclient import testing as t
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
from lnhistoryclient.parser.common import get_message_type_by_bytes, strip_known_message_type
from lnhistoryclient.parser.parser_map import PARSER_MAP


def parse(msg_with_type: bytes):
    """Dispatch exactly like pipeline consumers: type from the wire, then PARSER_MAP."""
    msg_type = get_message_type_by_bytes(msg_with_type)
    assert msg_type is not None, "builder produced a type the library does not recognize"
    return msg_type, PARSER_MAP[msg_type](strip_known_message_type(msg_with_type))


def test_channel_announcement_roundtrip():
    msg = t.make_channel_announcement(scid=555, node1_seed=7, node2_seed=8, features=b"\x80")
    msg_type, parsed = parse(msg)
    assert msg_type == MSG_TYPE_CHANNEL_ANNOUNCEMENT
    assert parsed.scid == 555
    assert parsed.node_id_1 == t.make_pubkey(7)
    assert parsed.node_id_2 == t.make_pubkey(8)
    assert parsed.features == b"\x80"
    assert parsed.chain_hash == t.CHAIN_HASH_MAINNET[::-1]  # parser reverses to display order


def test_node_announcement_roundtrip():
    msg = t.make_node_announcement(node_seed=3, timestamp=1_711_111_111, alias=b"carol")
    msg_type, parsed = parse(msg)
    assert msg_type == MSG_TYPE_NODE_ANNOUNCEMENT
    assert parsed.node_id == t.make_pubkey(3)
    assert parsed.timestamp == 1_711_111_111
    assert parsed.alias.rstrip(b"\x00") == b"carol"


@pytest.mark.parametrize("htlc_max", [None, 21_000_000_000])
def test_channel_update_roundtrip(htlc_max):
    msg = t.make_channel_update(
        scid=777,
        direction=1,
        cltv_expiry_delta=144,
        htlc_minimum_msat=5_000,
        fee_base_msat=1_234,
        fee_proportional_millionths=99,
        htlc_maximum_msat=htlc_max,
    )
    msg_type, parsed = parse(msg)
    assert msg_type == MSG_TYPE_CHANNEL_UPDATE
    assert parsed.scid == 777
    assert parsed.channel_flags[0] & 1 == 1
    assert parsed.cltv_expiry_delta == 144
    assert parsed.fee_base_msat == 1_234
    assert parsed.fee_proportional_millionths == 99
    assert parsed.htlc_maximum_msat == htlc_max


def test_channel_amount_roundtrip():
    msg_type, parsed = parse(t.make_channel_amount(satoshis=123_456))
    assert msg_type == MSG_TYPE_CHANNEL_AMOUNT
    assert parsed.satoshis == 123_456


def test_delete_channel_roundtrip():
    """Regression: 4103 must hit parse_delete_channel, not the private-update parser."""
    msg_type, parsed = parse(t.make_delete_channel(scid=31337))
    assert msg_type == MSG_TYPE_DELETE_CHANNEL
    assert parsed.scid == 31337


def test_private_channel_update_roundtrip():
    """Regression: 4102 must hit parse_private_channel_update."""
    inner = t.make_channel_update(scid=888)
    msg_type, parsed = parse(t.make_private_channel_update(inner=inner))
    assert msg_type == MSG_TYPE_PRIVATE_CHANNEL_UPDATE
    assert parsed.update == inner


def test_private_channel_announcement_roundtrip():
    """Regression: 4104 must hit parse_private_channel_announcement."""
    inner = t.make_channel_announcement(scid=999)
    msg_type, parsed = parse(t.make_private_channel_announcement(amount_sat=42_000, inner=inner))
    assert msg_type == MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT
    assert parsed.amount_sat == 42_000
    assert parsed.announcement == inner


def test_channel_dying_roundtrip():
    msg_type, parsed = parse(t.make_channel_dying(scid=444, blockheight=850_123))
    assert msg_type == MSG_TYPE_CHANNEL_DYING
    assert parsed.scid == 444
    assert parsed.blockheight == 850_123


def test_gossip_store_ended_roundtrip_both_layouts():
    # pre-v26.06: 8-byte payload, no uuid
    msg_type, parsed = parse(t.make_gossip_store_ended(equivalent_offset=987_654))
    assert msg_type == MSG_TYPE_GOSSIP_STORE_ENDED
    assert parsed.equivalent_offset == 987_654 and parsed.uuid is None
    assert parsed.to_dict() == {"equivalent_offset": 987_654, "uuid": None}

    # v26.06 (gossip_store v16): 40-byte payload with the successor's uuid
    uuid = t.make_uuid(9)
    _, parsed = parse(t.make_gossip_store_ended(equivalent_offset=11, uuid=uuid))
    assert parsed.equivalent_offset == 11 and parsed.uuid == uuid
    assert parsed.to_dict()["uuid"] == uuid.hex()


def test_gossip_store_uuid_roundtrip():
    uuid = t.make_uuid(5)
    msg_type, parsed = parse(t.make_gossip_store_uuid(uuid))
    assert msg_type == MSG_TYPE_GOSSIP_STORE_UUID
    assert parsed.uuid == uuid
    assert parsed.to_dict() == {"uuid": uuid.hex()}


def test_every_parser_map_entry_has_a_builder_roundtrip():
    """Completeness guard: a new PARSER_MAP entry without a round-trip test fails here."""
    covered = {
        MSG_TYPE_CHANNEL_ANNOUNCEMENT,
        MSG_TYPE_NODE_ANNOUNCEMENT,
        MSG_TYPE_CHANNEL_UPDATE,
        MSG_TYPE_CHANNEL_AMOUNT,
        MSG_TYPE_PRIVATE_CHANNEL_UPDATE,
        MSG_TYPE_DELETE_CHANNEL,
        MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT,
        MSG_TYPE_GOSSIP_STORE_ENDED,
        MSG_TYPE_CHANNEL_DYING,
        MSG_TYPE_GOSSIP_STORE_UUID,
    }
    assert set(PARSER_MAP) == covered


def test_gossip_store_file_roundtrip(tmp_path):
    """End-to-end through the real store reader: build a v16 store (uuid record,
    live + deleted records), read it back with read_gossip_file."""
    from lnhistoryclient.parser.gossip_file import read_gossip_file

    live1 = t.make_channel_announcement(scid=1)
    dead = t.make_channel_update(scid=2)
    live2 = t.make_channel_update(scid=3)
    store = t.make_gossip_store(
        [live1, (dead, 0x8000), live2],  # middle record carries the DELETED bit
        store_uuid=t.make_uuid(1),
    )
    path = tmp_path / "gossip_store"
    path.write_bytes(store)

    msgs = list(read_gossip_file(str(path)))
    # deleted record skipped; uuid record + 2 live messages survive
    assert msgs == [t.make_gossip_store_uuid(t.make_uuid(1)), live1, live2]
