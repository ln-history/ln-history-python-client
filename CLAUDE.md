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

Tests: `.venv/bin/python -m pytest -q` (206 unit tests; no backend required).

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

`backend_url` falls back to `$LN_HISTORY_BACKEND_URL` then `http://localhost:5050`, and
`api_key` to `$LN_HISTORY_API_KEY`. The examples take no `--key` flag by design — set the
env var instead. The deployed API **does** enforce the key; the local dev backend does not.

#### The snapshot stream's framing is inconsistent — three shapes, all handled

`iter_snapshot_messages` never trusts the framing: it forms a candidate blob, parses it,
and accepts it only if the result is plausible, resyncing a byte at a time otherwise.
`_candidate_message` tries, in order, `type ++ payload`, `varint ++ type ++ payload`
(the varint's meaning drifts between imports — sometimes it counts the type, sometimes
not, and the value is ignored either way), and finally `_typeless_channel_update` for
`varint ++ payload` with the two type bytes stripped.

Two things not to touch without redoing the measurement (originally taken against the
2026-07-01 snapshot, scored against the database's own policy set):

1. **The type-less branch must require an exact length match.** The varint has to equal a
   legal `channel_update` payload length (128, or 136 with `htlc_maximum_msat`) with *no*
   tolerance for a TLV tail. Allowing 12 bytes of slack loses 18 real
   `channel_announcement`s, 32 bytes loses 137 — the branch starts matching arbitrary byte
   runs. Exact-match scores 99.974% policy recall against the database with the channel
   layer bit-for-bit unchanged. The ~20 blobs that are type-less *and* carry an inbound-fee
   TLV remain unrecoverable; that is the accepted trade.
2. **A dropped blob costs more than itself, and it also fabricates.** Losing one starts a
   byte-walk that mis-frames what follows, so the 4.4% of blobs that were type-less were
   costing **12.3%** of all live policies — 1.8 well-formed casualties each. That is why
   `tests/test_snapshot_reader.py` asserts on the messages *surrounding* a damaged blob,
   not just on the blob. The same walk also **invented 20 phantom nodes** in the
   2026-07-01 snapshot: `channel_update` payload fragments read as `node_announcement`s,
   because `_is_plausible` only checks that a pubkey starts `0x02`/`0x03`, never that it
   is on the curve. Their "keys" decode as fee and CLTV values. Curve validation would
   close that hole but needs a crypto dependency in a deliberately dependency-free parser;
   fixing the framing removed the cause instead.

The proper fix is still a backend data repair (normalise every `raw_gossip` blob to one
framing); `gossip_id = sha256(raw_gossip)` so rewriting bytes changes primary keys.

### Client-specific pathfinding (`analysis/clients/`)

Reproduces how LND / CLN / LDK / eclair each choose a route, by version.

- `base.py` — `WeightFunction` (the extension point), `EdgeContext`, `PathConstraints`,
  `ClientProfile`. Adding a client means writing a weight function + constraints, never a
  new router.
- `lnd.py` / `cln.py` / `ldk.py` / `eclair.py` — one module per implementation. Each
  docstring cites the upstream file/symbol its constants came from and lists where the
  motivating paper (arXiv:2410.13784v2) is **wrong**.
- `registry.py` — `client_profile("lnd-bimodal@0.18.0")` resolves a spec to the behaviour
  that version shipped. Default version is the paper's baseline, deliberately pinned.
- `analysis/routing.py::ClientRouter` — one search engine covering all of them.
- `analysis/compare.py::compare_clients` — paired comparison over shared trials.

Three structural facts that constrain the design:

1. **LND's cost is not additive** — it is `sum(weight) + attempt_cost / prod(P_e)`, one
   global term, not per-edge. `WeightFunction.attempt_cost_msat` returning 0 collapses this
   back to plain additive for the other clients.
2. **LDK's cost is not a sum either** — `max(total_fee, path_htlc_min) + total_penalty`.
   That is why `path_cost()` and `path_htlc_minimum_msat()` exist on `WeightFunction`.
3. **eclair's Case 2 divides by the cumulative path probability**, same shape as LND's, and
   it overrides `path_cost()` for that reason.

A `WeightFunction` instance is stateful for the duration of one search (`prepare()` caches
the chain tip and payment amount), so it is bound to one search at a time.

**Comparing clients on archived snapshots.** `RoutingIndex.routable_core(amount_sat)` is
the largest set of nodes that can all pay each other at that amount. Draw trials from it
(`build_trials(..., nodes=core)`) rather than uniformly: most archived nodes have no
`channel_update`, so a uniform draw spends ~90% of its trials on pairs *every* client
fails, which measures archive coverage rather than pathfinding. The core is amount-
dependent (`htlc_maximum_msat` is), so recompute it per amount rather than reusing one
pool across a sweep. It is deterministic by construction — ties broken on the smallest
member — because the paired design breaks silently if the pool moves between processes.

`ClientComparison.route_frame()` / `.hop_frame()` are the tidy serialisations; `route_key`
(ordered `scid`s) is the agreement test. Both keep failed trials as null rows, so a
`groupby(client)` cannot silently compare different trial sets.

### Payment feasibility (`analysis/mincut.py`)

Reproduces Rene Pickhardt's [upper bound on payment
feasibility](https://github.com/renepickhardt/Lightning-Network-Limitations): sample random
uniform liquidity states, pick random node pairs, and take the max-flow. `P(min-cut >= a)`
bounds how often a payment of `a` is possible *at all*, assuming a sender with full
knowledge of every balance.

- `sample_min_cuts(channels, trials)` → `MinCutSamples`; `feasibility` /
  `amount_at_service_level` are the two inverse readings of the result.
- `exact_feasibility` enumerates every liquidity state — toy graphs only, but it is what
  the sampler estimates, so `tests/test_mincut.py` pins the sampler to ground truth on the
  27-state example from the original notebook.
- `channel_table` merges parallel channels (capacity summed) as the original does;
  `induced_subgraph` + `professional_nodes` build the BOS "professional" subnetwork.

Two things not to change without reading the docstring:

1. **Do not swap in `scipy.sparse.csgraph.maximum_flow`.** It is int32-only and, given
   int64 capacities, returns `0` silently instead of raising. Real node strengths exceed
   int32 (500+ BTC), so it would quietly produce wrong answers. `igraph` (in the `analysis`
   extra) carries capacities as doubles — exact below 2^53 — and is ~60x faster than the
   `networkx` fallback, which is tested to agree with it exactly.
2. **This analysis needs no `channel_update` coverage** — only topology and capacity.
   Unlike routing, every graph is usable here regardless of policy coverage.

### Node activity and the strong core (`analysis/activity.py`)

Splits a snapshot into the nodes that can carry payments and the ones that can only send
and receive. `strong_core(graph, amount_sat)` is the headline API — a node-induced
subgraph copy holding ~9% of the nodes and ~58% of the channels.

- `node_profiles(graph, amount_sat)` → `node_id` → `NodeProfile` (channels, peers,
  capacity, live in/out directions, `max_forward_sat`, policy age, fee level, class).
- `ActivityClass` is `PASSIVE` / `MARGINAL` / `ACTIVE`; `ActivityRule` holds the rule.
- `activity_curve(profiles, amounts)` — how the capable set shrinks with payment size.

**`max_forward_sat` is the load-bearing quantity**: the largest single payment the node
could forward, which is a min-cut on its own star — a forward needs one channel in and a
*different* one out, so the ceiling is the **second-largest** usable channel, not the total
and not the largest. Usability comes from `routing._edge_passes`, the routers' own
predicate, so the two cannot drift.

Four design properties worth knowing before relying on the default rule:

1. **The default rule contains no fitted threshold, and that is the finding.** But be
   precise about the status of the claim: `max_forward_sat` is built from directions that
   already pass the constraints at that amount, so it is `0` or `>= amount` and never in
   between — "no forwarder below the gate" is mostly a *theorem*, and the label run is a
   correctness check on it rather than a discovery. (One real gap it does test: the gate
   checks at `amount` while a forward carries `amount + downstream fees`, so an
   `htlc_minimum` just above the amount could in principle slip through. None did.) The
   empirical content is the **magnitude** — 90.7% of nodes removed losslessly — and the
   fact that **56% of what survives is never used at all**.
2. **Neither test works alone.** 180 nodes pass the gate from outside the core and 1 169
   core members fail the gate; both groups forward nothing. Don't drop `require_core`.
   (The `in_core` containment is also structural: trials are drawn from the core, so any
   node between two core members is itself in the core. The sizing is what earns the test.)
3. **`max_forward_sat` is an exact gate but the *worst* ranker** (AUC 0.71 vs 0.82 for
   degree). If you need a smaller set, rank by `bidirectional`; `min_live_channels` is the
   knob, and every positive value is lossy.
4. **`policy_age_days` is deliberately unused by the rule.** Collector outages put holes in
   the update history at fixed *calendar* positions (an identical 37.9-day gap,
   2025-10-09..2025-11-16, appears in every later snapshot), so a staleness threshold would
   encode archive downtime as node behaviour.

**This analysis requires `channel_update` coverage** — unlike mincut, an unpriced
direction is unusable, so thin policy coverage manufactures passive nodes; screen a date's
coverage before drawing conclusions from it.

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
