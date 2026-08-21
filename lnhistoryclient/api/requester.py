"""HTTP client for the ln-history query API (``api.ln-history.info`` / local dev).

Rewritten for the current API surface (``ln-history/v1/...``). The snapshot endpoint
streams concatenated ``raw_gossip`` blobs, which this client parses directly into the
canonical :class:`networkx.MultiDiGraph`. The blob framing is currently inconsistent in
the backend (some blobs carry a varint length prefix and some do not, and the varint's
meaning even differs between sections), so :func:`iter_snapshot_messages` derives each
message's length from its BOLT #7 structure, validates each parse, and resyncs past
stray bytes rather than trusting the framing.

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
from lnhistoryclient.model.ChannelAnnouncement import ChannelAnnouncement
from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.NodeAnnouncement import NodeAnnouncement
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
        api_key: Optional ``x-api-key``. Falls back to the ``LN_HISTORY_API_KEY``
            environment variable, so the example scripts work against the deployed API
            without every one of them growing a ``--key`` flag. Not required when the
            backend runs with auth disabled (the default in local/dev deployments).
        backend_url: Base URL. Defaults to ``LN_HISTORY_BACKEND_URL`` if set, else
            ``http://localhost:5050``.
        timeout: Per-request timeout in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend_url: Optional[str] = None,
        timeout: int = 60,
    ):
        resolved = backend_url or os.environ.get("LN_HISTORY_BACKEND_URL") or DEFAULT_BACKEND_URL
        self.backend_url = resolved.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "lnhistoryclient-python"})
        key = api_key or os.environ.get("LN_HISTORY_API_KEY")
        if key:
            self.session.headers.update({"x-api-key": key})

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

#: Byte offset of ``message_flags`` inside a type-less ``channel_update`` payload:
#: signature 64 + chain_hash 32 + scid 8 + timestamp 4.
_CU_FLAGS_OFFSET = 108
#: ``channel_update`` payload length excluding the optional ``htlc_maximum_msat``:
#: the 108 above + message_flags 1 + channel_flags 1 + cltv 2 + htlc_min 8 + fee 4 + 4.
_CU_BASE_PAYLOAD = 128
_CHANNEL_UPDATE_TYPE_BYTES = b"\x01\x02"


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from(">H", buf, off)[0]


def _typeless_channel_update(buf: bytes, pos: int, size: int) -> Optional[tuple]:
    """Recover a ``varint(payload_len) ++ payload`` blob whose 2-byte type was stripped.

    A third framing variant present in the archive: some ``channel_update`` blobs carry
    the varint but not the type, so neither the unframed nor the framed probe in
    :func:`_candidate_message` can identify them. Measured against the database on the
    2026-07-01 snapshot, they are 4.4% of live policies — but losing one also derails the
    byte-at-a-time resync that follows it, which destroyed a further 1.8 well-formed
    messages each and cost 12.3% of all policies in total.

    The varint is required to equal the payload length **exactly**, with no tolerance for
    a TLV tail. That is what makes the branch safe: admitting even a 12-byte tail lets it
    match arbitrary byte runs and swallow real ``channel_announcement`` blobs (measured:
    18 channels lost at 12 bytes, 137 at 32). The exact-length rule recovers 99.97% of
    policies while leaving the channel layer bit-for-bit unchanged. The ~20 blobs that are
    both type-less *and* carry an inbound-fee TLV stay unrecoverable; that is the trade.

    Returns ``(message_bytes_incl_type, next_pos)``, or ``None`` when the shape does not
    match. The caller still parses and plausibility-checks the result.
    """
    stream = io.BytesIO(buf)
    stream.seek(pos)
    value = varint_decode(stream)
    body = stream.tell()
    if value is None or body <= pos or body + value > size:
        return None
    if value not in (_CU_BASE_PAYLOAD, _CU_BASE_PAYLOAD + 8):
        return None
    payload = buf[body : body + value]
    expected = _CU_BASE_PAYLOAD + (8 if payload[_CU_FLAGS_OFFSET] & 0x01 else 0)
    if value != expected:
        return None
    return _CHANNEL_UPDATE_TYPE_BYTES + payload, body + value


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


def _is_pubkey(b: bytes) -> bool:
    """A 33-byte compressed secp256k1 pubkey starts with 0x02 or 0x03."""
    return len(b) == 33 and b[0] in (2, 3)


def _is_plausible(parsed: ParsedMessage) -> bool:
    """Reject false-positive parses that arise when the reader lands inside a payload.

    Used to disambiguate real message boundaries from coincidental type bytes in the
    inconsistently-framed backend stream.
    """
    if isinstance(parsed, ChannelAnnouncement):
        return parsed.scid > 0 and _is_pubkey(parsed.node_id_1) and _is_pubkey(parsed.node_id_2)
    if isinstance(parsed, NodeAnnouncement):
        return _is_pubkey(parsed.node_id)
    if isinstance(parsed, ChannelUpdate):
        # sane sender timestamp (2014-05 .. 2036) and a real scid
        return parsed.scid > 0 and 1_400_000_000 <= parsed.timestamp <= 2_100_000_000
    return False


def _candidate_message(buf: bytes, pos: int, size: int) -> Optional[tuple]:
    """Return ``(message_bytes_incl_type, next_pos)`` for the blob at ``pos``.

    The backend's framing is inconsistent — even the varint's meaning differs between
    sections (payload-length for channels, whole-message-length for updates). So the
    varint value is *not* trusted for length: it is used only to locate the 2-byte type,
    after which the true length is computed from the message's own structure. The type
    may be at ``pos`` (unframed), just after a leading varint (framed), or absent
    altogether (see :func:`_typeless_channel_update`, tried last so it can never preempt
    a blob that one of the two type-bearing shapes already explains).
    """
    # Unframed: cursor is already on the type.
    if pos + 2 <= size and _u16(buf, pos) in _KNOWN_BOLT7_TYPES:
        length = _unframed_message_length(buf, pos)
        if length is not None and pos + length <= size:
            return buf[pos : pos + length], pos + length
    # Framed: skip the leading varint, then the type follows.
    stream = io.BytesIO(buf)
    stream.seek(pos)
    varint_value = varint_decode(stream)
    type_pos = stream.tell()
    if (
        varint_value is not None
        and type_pos > pos
        and type_pos + 2 <= size
        and _u16(buf, type_pos) in _KNOWN_BOLT7_TYPES
    ):
        length = _unframed_message_length(buf, type_pos)
        if length is not None and type_pos + length <= size:
            return buf[type_pos : type_pos + length], type_pos + length
    # Type-less: a varint whose value is exactly a channel_update payload length.
    return _typeless_channel_update(buf, pos, size)


def iter_snapshot_messages(path: str) -> Iterator[ParsedMessage]:
    """Yield parsed BOLT #7 messages from a downloaded snapshot stream.

    The stream concatenates ``raw_gossip`` blobs whose framing is currently
    **inconsistent** in the backend. Three shapes occur, and all three are handled:
    ``varint ++ type ++ payload`` (channels, and the varint sometimes counts the type and
    sometimes does not), bare ``type ++ payload`` (most node blobs), and
    ``varint ++ payload`` with the type stripped (see :func:`_typeless_channel_update`).
    This reader is adaptive and self-correcting: it forms a candidate blob, parses it, and
    accepts it only if the result is plausible (valid pubkey / scid / timestamp). On an
    implausible or unparseable candidate it advances one byte and resynchronises to the
    next real message, so stray framing bytes never derail it.

    That resync is not free, which is why the third shape matters more than its share
    suggests: on the 2026-07-01 snapshot the type-less blobs are 4.4% of live policies,
    but each one lost also cost ~1.8 well-formed neighbours to the byte-walk that
    followed. Handling them takes policy recall against the database from 87.7% to 99.97%.
    """
    with open(path, "rb") as f:
        buf = f.read()
    pos = 0
    size = len(buf)
    resyncs = 0
    while pos < size:
        candidate = _candidate_message(buf, pos, size)
        parsed: Optional[ParsedMessage] = None
        if candidate is not None:
            message, next_pos = candidate
            try:
                maybe = parse_raw_message(message)
            except Exception:
                maybe = None
            if maybe is not None and _is_plausible(maybe):
                parsed = maybe

        if parsed is not None:
            yield parsed
            pos = next_pos
        else:
            pos += 1  # resync past a stray framing byte / false type match
            resyncs += 1
    if resyncs:
        logger.debug("Resynced past %d stray byte(s) while parsing snapshot stream", resyncs)


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
