# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Format and lint (runs automatically via pre-commit)
black lnhistoryclient/
ruff check --fix lnhistoryclient/

# Type checking
mypy lnhistoryclient/

# Version bumping (uses commitizen)
cz bump

# Install pre-commit hooks
pre-commit install
```

There are no automated tests yet — `tests/` directory exists but is empty.

## Architecture

This is a Python library for parsing raw Lightning Network gossip messages (BOLT #7). It has two main concerns:

1. **Parsing** — converting raw bytes from gossip files or network messages into typed Python dataclasses
2. **Querying** — `LnhistoryRequester` fetches historical LN snapshots from the `api.ln-history.info` API

### Message types

Defined in `constants.py`:
- **BOLT #7 standard types** (256–258): `channel_announcement`, `node_announcement`, `channel_update`
- **Core Lightning internal types** (4101–4106): `channel_amount`, `private_channel_announcement`, `private_channel_update`, `delete_channel`, `gossip_store_ended`, `channel_dying`
- **LND TLV extension** (type 55555): inbound fees carried in `channel_update` TLV tail

### Parsing pipeline

```
raw bytes
  → get_message_type_by_bytes()   # reads 2-byte big-endian type prefix
  → PARSER_MAP[type]              # maps int → parser function
  → strip_known_message_type()    # removes 2-byte prefix before calling parser
  → parse_*(bytes | BytesIO)      # returns typed dataclass
```

- `parser/parser_factory.py` — `parse_gossip_msg()` is the single-call convenience entry point; `get_parser_from_bytes()` / `get_parser_by_message_type()` for manual dispatch
- `parser/parser_map.py` — the canonical `PARSER_MAP` dict and `GOSSIP_TYPE_TO_PARSED_TYPE` dict
- `parser/parser.py` — BOLT #7 parsers (channel_announcement, node_announcement, channel_update)
- `parser/core_lightning_internal/parser.py` — CLN-internal message parsers
- `parser/lnd_internal/parser.py` — LND extension parser

### Gossip file reading

`parser/gossip_file.py` — `read_gossip_file(path, start=0)` auto-detects one of three formats:
- **GSP** (`GSP\x01` magic header) — Christian Decker's research format
- **Core Lightning gossip_store** — version byte with major=0 in high 3 bits
- **Plain** — big-endian varint-prefixed messages

Yields raw `bytes` per message; the caller is responsible for typing and parsing.

### Models

Two parallel model hierarchies both live under `model/`:

| Module | Description |
|---|---|
| `model/types.py` | `TypedDict` definitions for BOLT #7 messages and plugin events (`ParsedGossipDict`, `PluginEvent`) |
| `model/ChannelAnnouncement.py`, `ChannelUpdate.py`, `NodeAnnouncement.py` | Dataclasses with `.to_dict()` methods |
| `model/core_lightning_internal/types.py` | `TypedDict`s for CLN-internal messages |
| `model/lnd_internal/LNDExtension.py` | Dataclass for LND TLV inbound-fee extension |

Parsers return **dataclasses**. The `*Dict` `TypedDict` variants are used for serialisation and type-checking API payloads.

### LnhistoryRequester

`Lnhistoryrequester.py` — HTTP client wrapping `api.ln-history.info`. Requires an API key. Returns `networkx.DiGraph` snapshots or raw temp-file paths. Supports `dot`, `gml`, `graphml`, `json` output formats. Use as a context manager (`with LnhistoryRequester(...) as r:`).

### Utilities in `parser/common.py`

- `varint_decode` / `varint_encode` — Bitcoin-style variable-length integers (big- or little-endian)
- `get_scid_from_int` — decodes a short channel ID integer to `blockheight x txindex x output`
- `parse_address` — parses IPv4/IPv6/Tor v2/Tor v3/DNS node addresses
- `strip_known_message_type` — removes 2-byte type prefix if known

## Code style

- Line length: 120 (black + ruff)
- Type annotations required; `mypy --strict` is the target (currently disabled in pre-commit)
- Commit messages follow Conventional Commits (enforced by commitizen pre-commit hook)
- `# type: ignore` and `# mypy: ignore-errors` are used sparingly where strict mode is impractical
