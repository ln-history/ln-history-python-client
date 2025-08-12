import struct
from collections import defaultdict
from typing import Dict, Set

import networkx as nx

from lnhistoryclient.constants import MSG_TYPE_CHANNEL_ANNOUNCEMENT, MSG_TYPE_CHANNEL_UPDATE, MSG_TYPE_NODE_ANNOUNCEMENT
from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement
from lnhistoryclient.parser.common import read_exact, strip_known_message_type, varint_decode
from lnhistoryclient.parser.parser import parse_channel_announcement, parse_channel_update, parse_node_announcement


def get_latest_node_announcement(announcements: list) -> "NodeAnnouncement":
    """
    Get the latest node announcement based on timestamp.

    Args:
        announcements: List of NodeAnnouncement objects

    Returns:
        NodeAnnouncement: The announcement with the latest timestamp
    """
    return max(announcements, key=lambda x: x.timestamp)


def parse_gossip_messages(file_path: str):
    """Generator function that reads raw gossip bytes from a TLV formatted file and yields parsed messages."""
    counter_types = {MSG_TYPE_CHANNEL_ANNOUNCEMENT: 0, MSG_TYPE_NODE_ANNOUNCEMENT: 0, MSG_TYPE_CHANNEL_UPDATE: 0}

    with open(file_path, "rb") as f:
        while True:
            # Use varint_decode to determine the length of the message
            msg_length = varint_decode(f)
            if msg_length is None or msg_length == 1:
                break  # End of file or invalid length

            # Read the whole message value data based on the length; this should include the type
            message_data = f.read(msg_length)
            if len(message_data) != msg_length:
                raise ValueError(f"Incomplete message read from {file_path}")

            # Extract the message type (first 2 bytes of the TLV message)
            msg_type = struct.unpack(">H", message_data[:2])[0]

            # Remove the type prefix and length info, leaving only the message value
            msg_data = strip_known_message_type(
                message_data
            )  # assuming type is 2 bytes and length is not included in value part

            # Determine which parser to use based on message type
            counter_types[msg_type] += 1
            if msg_type == MSG_TYPE_CHANNEL_ANNOUNCEMENT:
                yield parse_channel_announcement(msg_data)

            elif msg_type == MSG_TYPE_NODE_ANNOUNCEMENT:
                yield parse_node_announcement(msg_data)

            elif msg_type == MSG_TYPE_CHANNEL_UPDATE:
                yield parse_channel_update(msg_data)

        print(f"counter_types {counter_types}")


def read_pg_copy_single_column_binary(filename: str):
    """
    Parses a PostgreSQL COPY BINARY format file that contains only one BYTEA column (raw_gossip).
    Yields raw_gossip bytes.
    """

    counter_types = {MSG_TYPE_CHANNEL_ANNOUNCEMENT: 0, MSG_TYPE_NODE_ANNOUNCEMENT: 0, MSG_TYPE_CHANNEL_UPDATE: 0}

    try:
        with open(filename, "rb") as file:
            header = read_exact(file, 11 + 4 + 4)  # 'PGCOPY\n' + flags + header extension length
            if not header.startswith(b"PGCOPY\n"):
                raise ValueError("Not a PostgreSQL COPY binary file")

            while True:
                # Read number of columns (int16)
                raw = read_exact(file, 2)
                if raw == b"\xff\xff":  # end of data
                    break
                if len(raw) < 2:
                    raise ValueError("Unexpected end of file while reading column count")
                column_count = int.from_bytes(raw, byteorder="big")

                if column_count != 1:
                    raise ValueError(f"Expected 1 column, got {column_count}")

                # Read the column data length (int32)
                col_len_bytes = read_exact(file, 4)
                if len(col_len_bytes) < 4:
                    raise ValueError("Unexpected end of file while reading column length")

                col_len = int.from_bytes(col_len_bytes, byteorder="big")
                if col_len == -1:
                    # NULL value
                    continue

                msg = read_exact(file, col_len)
                if len(msg) != col_len:
                    raise ValueError("Incomplete message data")

                # Extract the message type (first 2 bytes of the TLV message)
                msg_type = struct.unpack(">H", msg[:2])[0]

                # Remove the type prefix and length info, leaving only the message value
                msg_data = strip_known_message_type(
                    msg
                )  # assuming type is 2 bytes and length is not included in value part

                # Determine which parser to use based on message type
                counter_types[msg_type] += 1
                if msg_type == MSG_TYPE_CHANNEL_ANNOUNCEMENT:
                    yield parse_channel_announcement(msg_data)

                elif msg_type == MSG_TYPE_NODE_ANNOUNCEMENT:
                    yield parse_node_announcement(msg_data)

                elif msg_type == MSG_TYPE_CHANNEL_UPDATE:
                    yield parse_channel_update(msg_data)

    except Exception as e:
        print(f"Error while reading postgres data file {e}")
        raise e


def create_network_graph(file_path: str, use_postgres_format: bool = False) -> nx.DiGraph:
    """
    Creates a directed NetworkX graph from TLV formatted gossip messages in the file.

    Returns:
        nx.DiGraph: A directed graph representing the Lightning Network
    """
    G = nx.DiGraph()  # Use directed graph

    # Storage for processing messages
    node_announcements: Dict[str, list] = defaultdict(list)  # node_id -> list of announcements
    channel_announcements: Dict[str, "ChannelAnnouncement"] = {}  # scid -> announcement
    channel_updates: Dict[tuple, "ChannelUpdate"] = {}  # (scid, direction) -> update

    # Counters for logging
    node_announcement_counts: Dict[str, int] = defaultdict(int)

    print("First pass: collecting all messages...")

    iterator_function = (
        read_pg_copy_single_column_binary(file_path) if use_postgres_format else parse_gossip_messages(file_path)
    )

    # First pass: collect all messages
    for message in iterator_function:
        if isinstance(message, ChannelAnnouncement):
            channel_announcements[message.scid_str] = message

        elif isinstance(message, NodeAnnouncement):
            node_id_hex = message.node_id.hex()
            node_announcements[node_id_hex].append(message)
            node_announcement_counts[node_id_hex] += 1

        elif isinstance(message, ChannelUpdate):
            # Use (scid, direction) as key for channel updates
            key = (message.scid_str, message.direction)
            # Keep only the latest update for each (scid, direction) pair
            if key not in channel_updates or message.timestamp > channel_updates[key].timestamp:
                channel_updates[key] = message

    # Log node announcement statistics
    print("\nNode announcement statistics:")
    print(f"Total unique nodes with announcements: {len(node_announcements)}")
    multiple_announcements = {node_id: count for node_id, count in node_announcement_counts.items() if count > 1}
    if multiple_announcements:
        print(f"Nodes with multiple announcements: {len(multiple_announcements)}")
        for node_id, count in sorted(multiple_announcements.items(), key=lambda x: x[1], reverse=True)[:5]:
            print(f"  {node_id}: {count} announcements")

    print("\nSecond pass: building graph...")

    # Get latest node announcements
    latest_node_announcements: Dict[str, "NodeAnnouncement"] = {}
    for node_id, announcements in node_announcements.items():
        latest_node_announcements[node_id] = get_latest_node_announcement(announcements)

    # Track all node IDs that appear in channels (announced or not)
    all_channel_nodes: Set[str] = set()

    # Add channels (edges) to the graph
    for scid, channel_ann in channel_announcements.items():
        node1_hex = channel_ann.node_id_1.hex()
        node2_hex = channel_ann.node_id_2.hex()

        all_channel_nodes.add(node1_hex)
        all_channel_nodes.add(node2_hex)

        # Create channel attributes from announcement
        channel_attrs = {
            "scid": scid,
            "features": channel_ann.features.hex(),
            "chain_hash": channel_ann.chain_hash.hex(),
            "bitcoin_key_1": channel_ann.bitcoin_key_1.hex(),
            "bitcoin_key_2": channel_ann.bitcoin_key_2.hex(),
            "node_signature_1": channel_ann.node_signature_1.hex(),
            "node_signature_2": channel_ann.node_signature_2.hex(),
            "bitcoin_signature_1": channel_ann.bitcoin_signature_1.hex(),
            "bitcoin_signature_2": channel_ann.bitcoin_signature_2.hex(),
        }

        # Add directed edges for both directions
        # Direction 0: node1 -> node2
        edge_attrs_0 = channel_attrs.copy()
        edge_attrs_0["direction"] = 0

        # Direction 1: node2 -> node1
        edge_attrs_1 = channel_attrs.copy()
        edge_attrs_1["direction"] = 1

        # Check for channel updates and add them to edge attributes
        update_key_0 = (scid, 0)
        update_key_1 = (scid, 1)

        if update_key_0 in channel_updates:
            update = channel_updates[update_key_0]
            edge_attrs_0.update(
                {
                    "timestamp": update.timestamp,
                    "cltv_expiry_delta": update.cltv_expiry_delta,
                    "htlc_minimum_msat": update.htlc_minimum_msat,
                    "fee_base_msat": update.fee_base_msat,
                    "fee_proportional_millionths": update.fee_proportional_millionths,
                    "htlc_maximum_msat": update.htlc_maximum_msat,
                    "message_flags": update.message_flags.hex(),
                    "channel_flags": update.channel_flags.hex(),
                    "has_update": True,
                }
            )
        else:
            edge_attrs_0["has_update"] = False

        if update_key_1 in channel_updates:
            update = channel_updates[update_key_1]
            edge_attrs_1.update(
                {
                    "timestamp": update.timestamp,
                    "cltv_expiry_delta": update.cltv_expiry_delta,
                    "htlc_minimum_msat": update.htlc_minimum_msat,
                    "fee_base_msat": update.fee_base_msat,
                    "fee_proportional_millionths": update.fee_proportional_millionths,
                    "htlc_maximum_msat": update.htlc_maximum_msat,
                    "message_flags": update.message_flags.hex(),
                    "channel_flags": update.channel_flags.hex(),
                    "has_update": True,
                }
            )
        else:
            edge_attrs_1["has_update"] = False

        # Add the directed edges
        G.add_edge(node1_hex, node2_hex, **edge_attrs_0)
        G.add_edge(node2_hex, node1_hex, **edge_attrs_1)

    # Add all nodes (both announced and unannounced)
    announced_nodes = set(latest_node_announcements.keys())
    unannounced_channel_nodes = all_channel_nodes - announced_nodes

    # Add announced nodes with their attributes
    for node_id, node_ann in latest_node_announcements.items():
        node_attrs = {
            "announced": True,
            "timestamp": node_ann.timestamp,
            "features": node_ann.features.hex(),
            "rgb_color": node_ann.rgb_color.hex(),
            "alias": node_ann.alias.decode("utf-8", errors="ignore").rstrip("\x00"),
            "addresses": [addr.to_dict() for addr in node_ann._parse_addresses()],
            "signature": node_ann.signature.hex(),
        }
        G.add_node(node_id, **node_attrs)

    # Add unannounced nodes (nodes that appear in channels but have no announcement)
    for node_id in unannounced_channel_nodes:
        G.add_node(node_id, announced=False)

    # Add isolated announced nodes (nodes with announcements but no channels)
    isolated_announced_nodes = announced_nodes - all_channel_nodes
    # These are already added above in the announced nodes loop

    # Print statistics
    print("\nGraph construction complete!")
    print(f"Total nodes: {G.number_of_nodes()}")
    print(f"  - Announced nodes: {len(announced_nodes)}")
    print(f"  - Unannounced nodes (in channels): {len(unannounced_channel_nodes)}")
    print(f"  - Isolated announced nodes (no channels): {len(isolated_announced_nodes)}")
    print(f"Total directed edges: {G.number_of_edges()}")
    print(f"Total channels: {len(channel_announcements)}")
    print(f"Total channel updates: {len(channel_updates)}")

    return G


def analyze_graph(G: nx.DiGraph):
    """
    Perform basic analysis on the Lightning Network graph.

    Args:
        G: The NetworkX DiGraph representing the Lightning Network
    """
    print("\n=== Graph Analysis ===")
    print(f"Nodes: {G.number_of_nodes()}")
    print(f"Edges: {G.number_of_edges()}")

    # Check for announced vs unannounced nodes
    announced_nodes = [n for n, attrs in G.nodes(data=True) if attrs.get("announced", False)]
    unannounced_nodes = [n for n, attrs in G.nodes(data=True) if not attrs.get("announced", False)]

    print(f"Announced nodes: {len(announced_nodes)}")
    print(f"Unannounced nodes: {len(unannounced_nodes)}")

    # Check edges with/without updates
    edges_with_updates = [(u, v) for u, v, attrs in G.edges(data=True) if attrs.get("has_update", False)]
    edges_without_updates = [(u, v) for u, v, attrs in G.edges(data=True) if not attrs.get("has_update", False)]

    print(f"Edges with channel updates: {len(edges_with_updates)}")
    print(f"Edges without channel updates: {len(edges_without_updates)}")

    # Basic connectivity
    if G.number_of_nodes() > 0:
        # Convert to undirected for connectivity analysis
        G_undirected = G.to_undirected()
        if nx.is_connected(G_undirected):
            print("Graph is connected")
        else:
            components = list(nx.connected_components(G_undirected))
            print(f"Graph has {len(components)} connected components")
            largest_component = max(components, key=len)
            print(f"Largest component has {len(largest_component)} nodes")


# Main execution
if __name__ == "__main__":
    # Path to the raw gossip binary file
    binary_file_path = "snapshot_diff_from-20221213_000000_to-20221213_000000 (1).bin"
    is_postgre_copy = True

    try:
        graph = create_network_graph(binary_file_path, is_postgre_copy)
        analyze_graph(graph)

        # Example: Access node information
        print("\n=== Sample Node Information ===")
        sample_nodes = list(graph.nodes(data=True))[:3]
        for node_id, attrs in sample_nodes:
            print(f"Node {node_id[:16]}...")
            for key, value in attrs.items():
                if key == "addresses" and isinstance(value, list):
                    print(f"  {key}: {len(value)} addresses")
                elif isinstance(value, str) and len(value) > 50:
                    print(f"  {key}: {value[:47]}...")
                else:
                    print(f"  {key}: {value}")
            print()

        # Example: Access edge information
        print("=== Sample Edge Information ===")
        sample_edges = list(graph.edges(data=True))[:2]
        for u, v, attrs in sample_edges:
            print(f"Edge {u[:16]}... -> {v[:16]}...")
            for key, value in attrs.items():
                if isinstance(value, str) and len(value) > 50:
                    print(f"  {key}: {value[:47]}...")
                else:
                    print(f"  {key}: {value}")
            print()

    except Exception as e:
        print(f"Error processing file: {e}")
        raise
