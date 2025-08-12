import json
import os
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, Optional, Union

import networkx as nx
import requests

from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement

FORMATS = ["dot", "gml", "graphml", "json"]


class LnhistoryRequesterError(Exception):
    """Custom exception for LnhistoryRequester errors"""

    pass


class LnhistoryRequester:
    def __init__(self, api_key: str, backend_url: Optional[str] = None, default_format: str = "json"):
        """
        Initialize the Lightning Network History API client.

        Args:
            api_key: API key for authentication
            backend_url: Backend URL (defaults to https://apiv2.ln-history.info)
            default_format: Default format for responses (json, dot, gml, graphml)
        """
        if not api_key:
            raise ValueError("API key is required")

        if default_format not in FORMATS:
            raise ValueError(f"Format must be one of: {FORMATS}")

        self.api_key: str = api_key
        self.backend_url: str = backend_url if backend_url else "https://apiv2.ln-history.info"
        self.default_format: str = default_format
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": self.api_key, "User-Agent": "LnhistoryRequester-Python-Client"})

    def _sanitize_graph_attributes(self, graph: nx.Graph, target_format: str) -> nx.Graph:
        """
        Sanitize graph data for formats with restricted attribute types (e.g., GraphML).
        - Converts list/set/dict to string
        - Replaces None with empty string (GraphML doesn't support None)
        """
        restricted_formats = {"graphml"}  # Add more formats if necessary
        if target_format.lower() not in restricted_formats:
            return graph  # No sanitization needed

        for _, attr in graph.nodes(data=True):
            for k, v in list(attr.items()):
                if v is None:
                    attr[k] = ""  # or "null" if you want explicit placeholder
                elif isinstance(v, (list, set, dict)):
                    attr[k] = str(v)

        for _, _, attr in graph.edges(data=True):
            for k, v in list(attr.items()):
                if v is None:
                    attr[k] = ""
                elif isinstance(v, (list, set, dict)):
                    attr[k] = str(v)

        return graph

    def _format_timestamp(self, timestamp: datetime) -> str:
        """Convert datetime to ISO 8601 format expected by the API"""
        return timestamp.isoformat()

    def _download_to_temp_file(self, endpoint: str) -> str:
        """
        Download binary data from API endpoint to a temporary file.

        Args:
            endpoint: API endpoint path

        Returns:
            Path to temporary file containing the data

        Raises:
            LnhistoryRequesterError: If request fails
        """
        url = f"{self.backend_url.rstrip('/')}/{endpoint.lstrip('/')}"

        try:
            response = self.session.get(url, timeout=30, stream=True)
            response.raise_for_status()

            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pg_copy")

            # Write binary data to temporary file
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    temp_file.write(chunk)

            temp_file.close()
            return temp_file.name

        except requests.exceptions.RequestException as e:
            raise LnhistoryRequesterError(f"Request failed: {str(e)}") from e

    def _save_graph_to_format(self, graph: nx.DiGraph, output_path: str, format: str) -> None:
        """
        Save NetworkX graph to specified format.

        Args:
            graph: NetworkX DiGraph to save
            output_path: Output file path
            format: Output format (dot, gml, graphml, json)
        """
        if format == "dot":
            nx.nx_agraph.write_dot(graph, output_path)
        elif format == "gml":
            nx.write_gml(graph, output_path)
        elif format == "graphml":
            nx.write_graphml(graph, output_path)
        elif format == "json":
            # Convert to JSON format compatible with NetworkX
            data = nx.node_link_data(graph)
            with open(output_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported format: {format}")

    def get_snapshot_at_timestamp(
        self,
        timestamp: datetime,
        return_graph: bool = True,
        save_to_file: Optional[str] = None,
        format: Optional[str] = None,
        stopwatch: bool = False,
    ) -> Union[nx.DiGraph, str]:
        timestamp_str = self._format_timestamp(timestamp)
        endpoint = f"ln-history/v1/LightningNetwork/snapshot/{timestamp_str}/copy"

        # Measure download time if needed
        if stopwatch:
            t0 = time.perf_counter()
        temp_file_path = self._download_to_temp_file(endpoint)
        if stopwatch:
            t1 = time.perf_counter()
            print(f"[Stopwatch] Download took {t1 - t0:.4f} seconds")

        try:
            if return_graph:
                from lnhistoryclient.common import create_network_graph

                if stopwatch:
                    t2 = time.perf_counter()
                graph = create_network_graph(temp_file_path, use_postgres_format=True)
                if stopwatch:
                    t3 = time.perf_counter()
                    print(f"[Stopwatch] Graph creation took {t3 - t2:.4f} seconds")

                # Save to file if requested
                if save_to_file:
                    save_format = format if format else self.default_format
                    if save_format not in FORMATS:
                        raise ValueError(f"Format must be one of: {FORMATS}")

                    # ✅ Automatically sanitize for restricted formats like GraphML
                    graph = self._sanitize_graph_attributes(graph, save_format)

                    self._save_graph_to_format(graph, save_to_file, save_format)

                return graph
            else:
                return temp_file_path

        except Exception as e:
            if return_graph and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            raise LnhistoryRequesterError(f"Failed to process snapshot data: {str(e)}") from e
        finally:
            if return_graph and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def get_snapshot_diff_for_start_end_timestamps(
        self,
        start_timestamp: datetime,
        end_timestamp: datetime,
        return_graph: bool = True,
        save_to_file: Optional[str] = None,
        format: Optional[str] = None,
    ) -> Union[nx.DiGraph, str]:
        """
        Get the difference between two Lightning Network snapshots as parsed JSON.

        Args:
            start_timestamp: Start timestamp for the diff
            end_timestamp: End timestamp for the diff

        Returns:
            dict: {
                "node_announcements": [...],
                "channel_announcements": [...],
                "channel_updates": [...]
            }
        """
        if start_timestamp >= end_timestamp:
            raise ValueError("Start timestamp must be before end timestamp")

        start_str = self._format_timestamp(start_timestamp)
        end_str = self._format_timestamp(end_timestamp)
        endpoint = f"ln-history/v1/LightningNetwork/snapshot/diff/{start_str}/{end_str}/copy"

        # Download data to temporary file
        temp_file_path = self._download_to_temp_file(endpoint)

        try:
            results = {"node_announcements": [], "channel_announcements": [], "channel_updates": []}
            from lnhistoryclient.common import parse_gossip_messages

            for msg in parse_gossip_messages(temp_file_path):

                if isinstance(msg, NodeAnnouncement):
                    results["node_announcements"].append(msg.to_dict())
                elif isinstance(msg, ChannelAnnouncement):
                    results["channel_announcements"].append(msg.to_dict())
                elif isinstance(msg, ChannelUpdate):
                    results["channel_updates"].append(msg.to_dict())

            return results

        except Exception as e:
            raise LnhistoryRequesterError(f"Failed to process diff data: {str(e)}") from e
        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def get_node_information_by_node_id(
        self, node_id: str, timestamp: datetime, format: Optional[str] = None
    ) -> Union[Dict[Any, Any], str]:
        """
        Get information about a specific node at a given timestamp.
        Note: This returns raw data, not a graph since it's node-specific information.

        Args:
            node_id: The Lightning Network node ID
            timestamp: The timestamp for the node information
            format: Response format (overrides default if provided)

        Returns:
            Node information data
        """
        if not node_id:
            raise ValueError("Node ID is required")

        used_format = format if format else self.default_format
        if used_format not in FORMATS:
            raise ValueError(f"Format must be one of: {FORMATS}")

        timestamp_str = self._format_timestamp(timestamp)
        endpoint = f"ln-history/v1/Node/{node_id}/info/{timestamp_str}/copy"

        # For node info, we'll download and process as binary data
        temp_file_path = self._download_to_temp_file(endpoint)

        try:
            # Read the binary data (you may want to process this differently based on your needs)
            with open(temp_file_path, "rb") as f:
                data = f.read()

            # Return based on requested format
            if used_format == "json":
                # You might want to process this binary data differently
                return {"raw_data": data.hex(), "size": len(data)}
            else:
                return data.hex()

        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def get_channel_information_by_scid(
        self, scid: str, timestamp: datetime, format: Optional[str] = None
    ) -> Union[Dict[Any, Any], str]:
        """
        Get information about a specific channel by short channel ID (scid) at a given timestamp.
        Note: This returns raw data, not a graph since it's channel-specific information.

        Args:
            scid: The short channel ID
            timestamp: The timestamp for the channel information
            format: Response format (overrides default if provided)

        Returns:
            Channel information data
        """
        if not scid:
            raise ValueError("SCID is required")

        used_format = format if format else self.default_format
        if used_format not in FORMATS:
            raise ValueError(f"Format must be one of: {FORMATS}")

        timestamp_str = self._format_timestamp(timestamp)
        endpoint = f"ln-history/v1/Channel/{scid}/info/{timestamp_str}/copy"

        # For channel info, we'll download and process as binary data
        temp_file_path = self._download_to_temp_file(endpoint)

        try:
            # Read the binary data
            with open(temp_file_path, "rb") as f:
                data = f.read()

            # Return based on requested format
            if used_format == "json":
                return {"raw_data": data.hex(), "size": len(data)}
            else:
                return data.hex()

        finally:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def close(self):
        """Close the HTTP session"""
        if self.session:
            self.session.close()

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
