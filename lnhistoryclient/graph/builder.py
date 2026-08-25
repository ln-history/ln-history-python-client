"""Build the canonical lossless :class:`networkx.MultiDiGraph` from gossip messages.

The builder is pure with respect to I/O: :func:`build_multidigraph` consumes already
parsed messages so it can be fed from any source (a downloaded snapshot, a gossip
file, or the API). :func:`build_graph_from_gossip_file` is a convenience wrapper that
reads and parses a plain/gossip_store/GSP file first.

Edge model
----------
Every channel becomes **two** directed edges in a ``MultiDiGraph``, both keyed by the
integer ``scid`` so parallel channels between the same pair of nodes are preserved
rather than overwritten:

* ``node_1 -> node_2`` with ``direction = 0``
* ``node_2 -> node_1`` with ``direction = 1``

Each edge carries the latest ``channel_update`` policy seen for that direction (fees,
cltv, htlc bounds, disabled bit). Capacity is not present in BOLT #7 gossip; the
``htlc_maximum_msat`` value is recorded as a proxy and ``capacity_sat`` is left
``None`` until :func:`lnhistoryclient.graph.enrich.attach_capacity` fills it in.
"""

import logging
import struct
from collections import defaultdict
from typing import Dict, Iterable, Optional, Tuple, Union

import networkx as nx

from lnhistoryclient.constants import (
    MSG_TYPE_CHANNEL_ANNOUNCEMENT,
    MSG_TYPE_CHANNEL_UPDATE,
    MSG_TYPE_NODE_ANNOUNCEMENT,
)
from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement
from lnhistoryclient.parser.common import get_message_type_by_bytes, strip_known_message_type
from lnhistoryclient.parser.gossip_file import read_gossip_file
from lnhistoryclient.parser.parser import (
    parse_channel_announcement,
    parse_channel_update,
    parse_node_announcement,
)

logger = logging.getLogger(__name__)

ParsedMessage = Union[NodeAnnouncement, ChannelAnnouncement, ChannelUpdate]

# channel_flags bit 1 (0x02) marks the direction as disabled.
_CHANNEL_DISABLED_BIT = 0x02


def _is_disabled(channel_flags: bytes) -> bool:
    """Return True when the ``disabled`` bit is set in a channel_update's flags."""
    return bool(channel_flags and (channel_flags[0] & _CHANNEL_DISABLED_BIT))


def _policy_attrs(update: ChannelUpdate) -> Dict[str, object]:
    """Extract the routing-relevant policy fields from a channel_update."""
    return {
        # As advertised on THIS edge, i.e. by the node that authored this update. See
        # build_multidigraph's `inbound_fees` argument for why the routing-relevant
        # value lives on the reverse edge instead.
        "inbound_fee_base_msat": update.inbound_fee_base_msat,
        "inbound_fee_proportional_millionths": update.inbound_fee_proportional_millionths,
        "has_update": True,
        "timestamp": update.timestamp,
        "cltv_expiry_delta": update.cltv_expiry_delta,
        "htlc_minimum_msat": update.htlc_minimum_msat,
        "htlc_maximum_msat": update.htlc_maximum_msat,
        "fee_base_msat": update.fee_base_msat,
        "fee_proportional_millionths": update.fee_proportional_millionths,
        "message_flags": update.message_flags.hex(),
        "channel_flags": update.channel_flags.hex(),
        "disabled": _is_disabled(update.channel_flags),
        # Proxy for capacity when no on-chain capacity_sat has been attached.
        # htlc_maximum_msat is in msat; capacity_sat is left None until enriched.
        "capacity_sat": None,
    }


def build_multidigraph(messages: Iterable[ParsedMessage], inbound_fees: bool = False) -> nx.MultiDiGraph:
    """Build the canonical lossless graph from an iterable of parsed gossip messages.

    Args:
        messages: Parsed ``NodeAnnouncement`` / ``ChannelAnnouncement`` /
            ``ChannelUpdate`` objects in any order. Other message types are ignored.
        inbound_fees: When True, additionally resolve lnd's inbound fees (TLV 55555) into
            the ``dst_inbound_fee_*`` edge attributes described below. Off by default so
            that graphs, and anything computed from them, are unchanged unless a caller
            opts in.

    Returns:
        A ``MultiDiGraph`` where node keys are hex-encoded node public keys and each
        channel contributes two directed edges keyed by its integer ``scid``.

    **Inbound fees sit on the reverse edge, and getting this backwards is easy.**

    A ``channel_update`` is authored by exactly one of the channel's two nodes and
    describes that node's policy. The inbound fee inside it is therefore the *author's*
    charge for HTLCs arriving at the author over that channel -- verified empirically
    against the archive, where a distinctive inbound-fee setting traces to a single
    author node 42.2% of the time against 6.8% for a single peer.

    So when a payment traverses ``u -> v``, the node charging an inbound fee is ``v``,
    and ``v``'s inbound fee for that channel is carried on the edge ``v -> u``. With
    ``inbound_fees=True`` that lookup is done once here and stored on ``u -> v`` as
    ``dst_inbound_fee_base_msat`` / ``dst_inbound_fee_proportional_millionths``, so
    consumers never have to reach across the graph to price a hop.

    The raw per-edge ``inbound_fee_*`` attributes (the author's own advertisement) are
    always populated when the update carried the record, regardless of this flag.
    """
    graph = nx.MultiDiGraph()

    node_announcements: Dict[str, NodeAnnouncement] = {}
    channel_announcements: Dict[int, ChannelAnnouncement] = {}
    channel_updates: Dict[Tuple[int, int], ChannelUpdate] = {}
    node_announcement_counts: Dict[str, int] = defaultdict(int)

    # First pass: collect the latest version of every message.
    for message in messages:
        if isinstance(message, ChannelAnnouncement):
            channel_announcements[message.scid] = message
        elif isinstance(message, NodeAnnouncement):
            node_id_hex = message.node_id.hex()
            node_announcement_counts[node_id_hex] += 1
            existing = node_announcements.get(node_id_hex)
            if existing is None or message.timestamp > existing.timestamp:
                node_announcements[node_id_hex] = message
        elif isinstance(message, ChannelUpdate):
            key = (message.scid, message.direction)
            existing_update = channel_updates.get(key)
            if existing_update is None or message.timestamp > existing_update.timestamp:
                channel_updates[key] = message

    # Second pass: add edges (channels), keyed by scid so parallels survive.
    channel_nodes: set[str] = set()
    for scid, announcement in channel_announcements.items():
        node1 = announcement.node_id_1.hex()
        node2 = announcement.node_id_2.hex()
        channel_nodes.add(node1)
        channel_nodes.add(node2)

        base_attrs = {
            "scid": scid,
            "scid_str": announcement.scid_str,
            "features": announcement.features.hex(),
        }

        for src, dst, direction in ((node1, node2, 0), (node2, node1, 1)):
            attrs = dict(base_attrs)
            attrs["direction"] = direction
            update = channel_updates.get((scid, direction))
            if update is not None:
                attrs.update(_policy_attrs(update))
            else:
                attrs["has_update"] = False
                attrs["disabled"] = False
                attrs["capacity_sat"] = None
            graph.add_edge(src, dst, key=scid, **attrs)

    if inbound_fees:
        # Resolve each edge's destination-charged inbound fee from the reverse edge. Done
        # as its own pass because it needs both directions to exist first.
        for src, dst, key, attrs in graph.edges(keys=True, data=True):
            reverse = graph.get_edge_data(dst, src, key)
            attrs["dst_inbound_fee_base_msat"] = (reverse or {}).get("inbound_fee_base_msat")
            attrs["dst_inbound_fee_proportional_millionths"] = (reverse or {}).get(
                "inbound_fee_proportional_millionths"
            )

    # Third pass: attach node attributes; add channel-only nodes without announcements.
    for node_id, announcement in node_announcements.items():
        graph.add_node(
            node_id,
            announced=True,
            timestamp=announcement.timestamp,
            alias=announcement.alias.decode("utf-8", errors="ignore").rstrip("\x00"),
            rgb_color=announcement.rgb_color.hex(),
            features=announcement.features.hex(),
            addresses=[addr.to_dict() for addr in announcement._parse_addresses()],
            signature=announcement.signature.hex(),
        )

    for node_id in channel_nodes - set(node_announcements):
        graph.add_node(node_id, announced=False)

    logger.info(
        "Built MultiDiGraph: %d nodes, %d directed edges, %d channels, %d updates",
        graph.number_of_nodes(),
        graph.number_of_edges(),
        len(channel_announcements),
        len(channel_updates),
    )
    return graph


def parse_raw_message(raw: bytes) -> Optional[ParsedMessage]:
    """Parse a single raw gossip message (with 2-byte type prefix) if it is a BOLT #7
    announcement/update; otherwise return ``None``."""
    if len(raw) < 2:
        return None
    msg_type = get_message_type_by_bytes(raw)
    if msg_type is None:
        # Fall back to the raw big-endian type prefix.
        msg_type = struct.unpack(">H", raw[:2])[0]
    payload = strip_known_message_type(raw)
    if msg_type == MSG_TYPE_CHANNEL_ANNOUNCEMENT:
        return parse_channel_announcement(payload)
    if msg_type == MSG_TYPE_NODE_ANNOUNCEMENT:
        return parse_node_announcement(payload)
    if msg_type == MSG_TYPE_CHANNEL_UPDATE:
        return parse_channel_update(payload)
    return None


def iter_parsed_messages(path_to_file: str, start: int = 0) -> Iterable[ParsedMessage]:
    """Yield parsed BOLT #7 messages from a gossip file (format auto-detected)."""
    for raw in read_gossip_file(path_to_file, start=start):
        parsed = parse_raw_message(raw)
        if parsed is not None:
            yield parsed


def build_graph_from_gossip_file(path_to_file: str, start: int = 0) -> nx.MultiDiGraph:
    """Read, parse, and build the canonical graph from a gossip file in one call.

    Supports the plain varint-delimited, Core Lightning ``gossip_store``, and GSP
    formats (auto-detected by :func:`read_gossip_file`).
    """
    return build_multidigraph(iter_parsed_messages(path_to_file, start=start))
