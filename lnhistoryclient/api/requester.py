"""HTTP client for the ln-history query API (``api.ln-history.info`` / local dev).

Rewritten for the current API surface (``ln-history/v1/...``). The snapshot endpoint
streams concatenated ``raw_gossip`` blobs, which this client parses directly into the
canonical :class:`networkx.MultiDiGraph`. The blob framing is currently inconsistent
in the backend (channel blobs carry a varint length prefix, node/update blobs do not),
so :func:`iter_snapshot_messages` parses adaptively and resyncs past stray bytes.

Capacity is not present in gossip. Pass ``enrich_capacity=True`` to
:meth:`LnhistoryRequester.get_snapshot` to spend one extra request against the bulk
``channels/capacities`` endpoint and attach on-chain ``capacity_sat`` to every channel.
"""

import io
import json
import logging
import os
import struct
import tempfile
from datetime import datetime
from typing import Dict, Iterator, Optional, Union

import networkx as nx
import requests

from lnhistoryclient.constants import (
    MSG_TYPE_CHANNEL_ANNOUNCEMENT,
    MSG_TYPE_CHANNEL_UPDATE,
    MSG_TYPE_NODE_ANNOUNCEMENT,
)
from lnhistoryclient.graph.builder import ParsedMessage, build_multidigraph, parse_raw_message
from lnhistoryclient.graph.enrich import attach_capacity
from lnhistoryclient.parser.common import varint_decode

logger = logging.getLogger(__name__)

DEFAULT_BACKEND_URL = "http://localhost:5050"
_API_PREFIX = "ln-history/v1"


class LnhistoryRequesterError(Exception):
    """Raised when an API request or response processing fails."""


def _format_timestamp(timestamp: Union[datetime, str]) -> str:
    """Format a datetime (or pass through ``"now"`` / preformatted string) for the API."""
    if isinstance(timestamp, datetime):
        return timestamp.isoformat()
    return str(timestamp)


class LnhistoryRequester:
    """Client for the ln-history REST API.

    Args:
        api_key: Optional ``x-api-key``. Not required when the backend runs with auth
            disabled (the default in local/dev deployments).
        backend_url: Base URL (defaults to ``http://localhost:5050``).
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend_url: str = DEFAULT_BACKEND_URL,
        timeout: int = 60,
    ):
        self.backend_url = backend_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "lnhistoryclient-python"})
        if api_key:
            self.session.headers.update({"x-api-key": api_key})

    # ── low-level helpers ────────────────────────────────────────────────────────

    def _url(self, endpoint: str) -> str:
        return f"{self.backend_url}/{_API_PREFIX}/{endpoint.lstrip('/')}"

    def _get(self, endpoint: str, params: Optional[Dict[str, object]] = None) -> requests.Response:
        try:
            response = self.session.get(self._url(endpoint), params=params, timeout=self.timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            raise LnhistoryRequesterError(f"Request to {endpoint} failed: {e}") from e

    def _get_json(self, endpoint: str, params: Optional[Dict[str, object]] = None) -> object:
        response = self._get(endpoint, params)
        try:
            return response.json()
        except json.JSONDecodeError as e:
            raise LnhistoryRequesterError(f"Invalid JSON from {endpoint}: {e}") from e

    def _download_to_temp(self, endpoint: str, params: Optional[Dict[str, object]] = None) -> str:
        """Stream a binary endpoint to a temporary file, returning its path."""
        try:
            response = self.session.get(self._url(endpoint), params=params, timeout=self.timeout, stream=True)
            response.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".gossip")
            for chunk in response.iter_content(chunk_size=1 << 16):
                if chunk:
                    tmp.write(chunk)
            tmp.close()
            return tmp.name
        except requests.exceptions.RequestException as e:
            raise LnhistoryRequesterError(f"Download from {endpoint} failed: {e}") from e

    # ── snapshot ─────────────────────────────────────────────────────────────────

    def get_snapshot(
        self,
        timestamp: Union[datetime, str],
        with_updates: bool = True,
        enrich_capacity: bool = False,
        return_graph: bool = True,
    ) -> Union[nx.MultiDiGraph, str]:
        """Fetch the network snapshot valid at ``timestamp``.

        Args:
            timestamp: Instant to snapshot (``datetime`` or ISO string or ``"now"``).
            with_updates: Include the latest ``channel_update`` per direction.
            enrich_capacity: If True, also fetch the bulk capacity map for the same
                timestamp and attach on-chain ``capacity_sat`` to every channel.
            return_graph: If False, return the raw downloaded file path instead of a graph.

        Returns:
            The canonical :class:`networkx.MultiDiGraph`, or the temp-file path when
            ``return_graph=False`` (caller owns the file).
        """
        ts = _format_timestamp(timestamp)
        params = {"withUpdates": str(with_updates).lower()}
        path = self._download_to_temp(f"snapshot/{ts}", params)

        if not return_graph:
            return path

        try:
            graph = build_multidigraph(iter_snapshot_messages(path))
            if enrich_capacity:
                capacities = self.get_capacities(timestamp)
                attach_capacity(graph, capacities)
            return graph
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def get_capacities(self, timestamp: Union[datetime, str]) -> Dict[int, int]:
        """Fetch the ``scid -> capacity_sat`` map for channels open at ``timestamp``."""
        ts = _format_timestamp(timestamp)
        payload = self._get_json("channels/capacities", {"timestamp": ts})
        return _parse_capacity_payload(payload)

    # ── point lookups (JSON) ─────────────────────────────────────────────────────

    def get_node(self, node_id: str, timestamp: Optional[Union[datetime, str]] = None) -> object:
        """Fetch a node's current/at-timestamp information (JSON)."""
        params = {"timestamp": _format_timestamp(timestamp)} if timestamp is not None else None
        return self._get_json(f"nodes/{node_id}", params)

    def get_channel(self, scid: str, timestamp: Optional[Union[datetime, str]] = None) -> object:
        """Fetch a channel's information by scid (JSON). ``scid`` may be int or ``BxTxO``."""
        params = {"timestamp": _format_timestamp(timestamp)} if timestamp is not None else None
        return self._get_json(f"channels/{scid}", params)

    def get_network_stats(self, timestamp: Optional[Union[datetime, str]] = None) -> object:
        """Fetch network-wide statistics (JSON)."""
        params = {"timestamp": _format_timestamp(timestamp)} if timestamp is not None else None
        return self._get_json("stats/network", params)

    # ── lifecycle ────────────────────────────────────────────────────────────────

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "LnhistoryRequester":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()


_KNOWN_BOLT7_TYPES = frozenset({MSG_TYPE_CHANNEL_ANNOUNCEMENT, MSG_TYPE_NODE_ANNOUNCEMENT, MSG_TYPE_CHANNEL_UPDATE})


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from(">H", buf, off)[0]


def _unframed_message_length(buf: bytes, off: int) -> Optional[int]:
    """Length (including the 2-byte type) of a self-delimiting BOLT #7 message that
    begins at ``off`` with no varint length prefix. Returns ``None`` if the type is
    unknown or the buffer is truncated.

    Handles the variable-length fields that make each message self-delimiting:
    ``channel_announcement``/``node_announcement`` features, node addresses, and the
    optional ``htlc_maximum_msat`` gated by ``message_flags`` in ``channel_update``.
    """
    if off + 2 > len(buf):
        return None
    msg_type = _u16(buf, off)
    body = off + 2  # first byte after the 2-byte type
    try:
        if msg_type == MSG_TYPE_CHANNEL_ANNOUNCEMENT:
            flen = _u16(buf, body + 256)  # after 4x64-byte signatures
            return 2 + 256 + 2 + flen + 32 + 8 + 33 * 4
        if msg_type == MSG_TYPE_NODE_ANNOUNCEMENT:
            flen = _u16(buf, body + 64)  # after signature
            addr_len_off = body + 64 + 2 + flen + 4 + 33 + 3 + 32  # +ts+node_id+rgb+alias
            alen = _u16(buf, addr_len_off)
            return (addr_len_off + 2 + alen) - off
        if msg_type == MSG_TYPE_CHANNEL_UPDATE:
            message_flags = buf[body + 64 + 32 + 8 + 4]  # after sig+chain+scid+timestamp
            base = 2 + 64 + 32 + 8 + 4 + 1 + 1 + 2 + 8 + 4 + 4
            return base + (8 if message_flags & 0x01 else 0)  # optional htlc_maximum_msat
    except (struct.error, IndexError):
        return None
    return None


def iter_snapshot_messages(path: str) -> Iterator[ParsedMessage]:
    """Yield parsed BOLT #7 messages from a downloaded snapshot stream.

    The stream concatenates ``raw_gossip`` blobs whose framing is currently
    inconsistent in the backend: channel blobs carry a little-endian varint payload
    length (``varint ++ type ++ payload``) while node/update blobs are unframed
    (``type ++ payload``). This reader is adaptive — it accepts a varint frame when the
    following bytes are a known type, and otherwise treats the bytes as a
    self-delimiting BOLT #7 message and computes its length from its structure.
    """
    with open(path, "rb") as f:
        buf = f.read()
    pos = 0
    size = len(buf)
    resyncs = 0
    while pos < size:
        message: Optional[bytes] = None
        next_pos = pos + 1  # default advance if this position turns out unparseable

        if pos + 2 <= size and _u16(buf, pos) in _KNOWN_BOLT7_TYPES:
            # Cursor is already on a message type: parse it structurally (self-delimiting).
            length = _unframed_message_length(buf, pos)
            if length is not None and pos + length <= size:
                message = buf[pos : pos + length]
                next_pos = pos + length
        else:
            # Not a type here — try a varint frame: varint(payload_len) ++ type ++ payload.
            stream = io.BytesIO(buf)
            stream.seek(pos)
            payload_len = varint_decode(stream)
            varint_end = stream.tell()
            if (
                payload_len is not None
                and varint_end + 2 + payload_len <= size
                and _u16(buf, varint_end) in _KNOWN_BOLT7_TYPES
            ):
                message = buf[varint_end : varint_end + 2 + payload_len]
                next_pos = varint_end + 2 + payload_len

        parsed = None
        if message is not None:
            try:
                parsed = parse_raw_message(message)
            except Exception:  # a false-positive framing guess — resync from the next byte
                parsed = None
                next_pos = pos + 1

        if parsed is None and next_pos != pos + 1:
            # We consumed a well-formed-looking blob but it was not a wanted type; keep it.
            pass
        elif parsed is None:
            resyncs += 1

        if parsed is not None:
            yield parsed
        pos = next_pos
    if resyncs:
        logger.debug("Resynced past %d stray framing byte(s) while parsing snapshot", resyncs)


def _parse_capacity_payload(payload: object) -> Dict[int, int]:
    """Normalise the capacities endpoint response into ``{int scid: capacity_sat}``.

    Accepts either a list of ``{"scid": ..., "capacity_sat": ...}`` objects or a plain
    ``{scid: capacity_sat}`` mapping.
    """
    result: Dict[int, int] = {}
    if isinstance(payload, dict):
        items: Iterator[tuple] = ((k, v) for k, v in payload.items())
        for scid, capacity in items:
            _add_capacity(result, scid, capacity)
    elif isinstance(payload, list):
        for entry in payload:
            if isinstance(entry, dict) and "scid" in entry:
                _add_capacity(result, entry["scid"], entry.get("capacity_sat"))
    else:
        raise LnhistoryRequesterError("Unexpected capacities response shape")
    return result


def _add_capacity(result: Dict[int, int], scid: object, capacity: object) -> None:
    """Insert one scid→capacity entry, coercing scid to int and skipping unusable rows."""
    if capacity is None:
        return
    try:
        scid_int = int(scid)
    except (TypeError, ValueError):
        return
    try:
        result[scid_int] = int(capacity)
    except (TypeError, ValueError):
        return


# Kept importable for callers that build graphs from a pre-downloaded stream file.
__all__ = [
    "LnhistoryRequester",
    "LnhistoryRequesterError",
    "iter_snapshot_messages",
    "DEFAULT_BACKEND_URL",
]
