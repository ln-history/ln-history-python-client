[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Checked with mypy](https://img.shields.io/badge/type%20checked-mypy-blue)](http://mypy-lang.org/)
![Uses: dataclasses](https://img.shields.io/badge/uses-dataclasses-brightgreen)
![Uses: typing](https://img.shields.io/badge/uses-typing-blue)

[![Commitizen friendly](https://img.shields.io/badge/commitizen-friendly-brightgreen.svg)](http://commitizen.github.io/cz-cli/)

# lnhistoryclient

A Python client library to **parse and handle raw Lightning Network gossip messages**. 
Reusable, and production-tested on real-world data. 
For details about the gossip messages see the Lightning Network specifications [BOLT #7](https://github.com/lightning/bolts/blob/master/07-routing-gossip.md)
This python package is part of the [ln-history](https://github.com/ln-history) project.

---


## Installation

```bash
pip install lnhistoryclient
```

## Usage

To parse a single raw Lightning Network gossip message, first extract the message type,
then use the type to select the appropriate parser. This ensures correctness
and avoids interpreting invalid data.
The library accepts both bytes and io.BytesIO objects as input for maximum flexibility.

```python
from lnhistoryclient.parser.common import get_message_type, strip_known_message_type
from lnhistoryclient.parser.parser_factory import get_parser_by_message_type


raw_hex = bytes.fromhex("0101...")  # Replace with actual raw hex (includes 2-byte type prefix)

msg_type = get_message_type_by_raw_hex(raw_hex)
if msg_type is not None:
    parser = get_parser_by_message_type(msg_type)
    result = parser(strip_known_message_type(raw_hex))  # Strip the type prefix
    print(result)
else:
    print("Unknown or unsupported message type.")
```

For convenience (and if you're confident the input is valid), a shortcut is also available:

```python
from lnhistoryclient.parser.parser_factory import get_parser_from_raw_hex
from lnhistoryclient.parser.common import strip_known_message_type

raw_hex = bytes.fromhex("0101...")  # Replace with actual raw hex

parser = get_parser_from_raw_hex(raw_hex)
if parser:
    result = parser(strip_known_message_type(raw_hex))
    print(result)
else:
    print("Could not determine parser.")
```

You can also directly use individual parsers if you know the message type:

```python
from lnhistoryclient.parser.channel_announcement_parser import parse_channel_announcement
from lnhistoryclient.parser.common import strip_known_message_type

result = parse_channel_announcement(strip_known_message_type(raw_hex))
print(result)
```

In case you have a file with multiple gossip messages there is also the `read_gossip_file` function available:

```python
from lnhistoryclient.parser.gossip_file import read_gossip_file
from lnhistoryclient.parser.common import get_message_type_by_bytes, strip_known_message_type
from lnhistoryclient.parser.parser_map import PARSER_MAP

for msg in read_gossip_file("path/to/your/gossip-file"):
    msg_type = get_message_type_by_bytes(msg)
    parse_func = PARSER_MAP[msg_type]
    parsed_msg = parse_func(strip_known_message_type(msg))
    print(parsed_msg)
```

Please see the doc string of the `read_gossip_file` for detailed information about the type of the gossip-file.
In short: Various file formats are supported and automatically detected.


## Graph building & analytics

Graph construction, network analytics, and the API client live under optional
submodules and require the `analysis` extra (the core parser stays dependency-free):

```bash
pip install "lnhistoryclient[analysis]"   # networkx, numpy, scipy, requests, pandas, matplotlib
```

Fetch a snapshot from the ln-history API and analyse it:

```python
from datetime import datetime
from lnhistoryclient.api import LnhistoryRequester
from lnhistoryclient.graph import graph_stats
from lnhistoryclient.analysis import Metric, top_nodes_by, simulate_random_payments

# backend_url falls back to $LN_HISTORY_BACKEND_URL then http://localhost:5050, and the
# api_key to $LN_HISTORY_API_KEY — the deployed API at https://api.ln-history.info needs one.
with LnhistoryRequester() as client:
    # enrich_capacity=True adds on-chain capacity_sat via the bulk capacities endpoint
    G = client.get_snapshot(datetime(2021, 6, 1), with_updates=True, enrich_capacity=True)

print(graph_stats(G))                                        # nodes/channels/components

# Node ranking — one entry point, metric + optional weighting:
top_nodes_by(G, Metric.BETWEENNESS)                          # unweighted routing importance
top_nodes_by(G, Metric.STRENGTH, weight="capacity")         # most liquidity
top_nodes_by(G, Metric.BETWEENNESS, weight="fee")           # cheapest-path centrality

# Payment simulation (balance-agnostic — an upper bound on routability):
summary = simulate_random_payments(G, n=1000, amount_sat=100_000, seed=42)
print(summary.success_rate, summary.failure_reasons)
```

The canonical graph is a lossless `networkx.MultiDiGraph` (`build_multidigraph`); use
`to_directed_simple` / `to_undirected_simple` to project it for routing or topology
metrics. See `examples/analyse_snapshot.py` for a full showcase. Weighting conventions
(fee cost, capacity inversion) live in `lnhistoryclient.analysis.weights`.

## Client-specific pathfinding

`simulate_payment` above finds the *cheapest* route. Real nodes do not: LND, Core
Lightning, LDK and eclair each trade fees against reliability, timelock and channel age
differently, and they disagree often enough to change which nodes matter. The
`analysis.clients` subpackage reproduces each one's weight function and side constraints,
so a snapshot can be routed as a **specific version of a specific client** would route it.

```python
from lnhistoryclient.analysis import (
    ClientRouter, build_trials, client_profile, compare_clients, simulate_payment,
)
from lnhistoryclient.analysis.routing import RoutingIndex

# Route as one client would
lnd = ClientRouter(client_profile("lnd", version="0.17.4"))
result = simulate_payment(G, alice, bob, amount_sat=100_000, strategy=lnd)
print(result.route.total_fee_msat, result.route.success_probability)

# Or compare many at once, over the same trials
comparison = compare_clients(G, ["lnd-apriori", "lnd-bimodal", "cln", "ldk", "eclair-ratios"],
                             n=500, amount_sat=100_000, seed=42)
print(comparison.to_frame())        # one row per client
print(comparison.route_frame())     # one row per (trial, client): fee, hops, cltv, route_key
print(comparison.hop_frame())       # one row per hop, for per-node fee ledgers
```

Two clients agree on a payment exactly when their `route_key` matches — it is the ordered
list of `scid`s, not of nodes, because two routes over the same nodes but different
parallel channels are genuinely different routes.

On an archived snapshot, draw trials from the **routable core** rather than uniformly.
Most nodes have no `channel_update` in the archive, so a uniform draw spends most of its
trials on pairs every client fails together — measuring archive coverage instead of
pathfinding:

```python
core = RoutingIndex(G).routable_core(amount_sat=100_000)   # largest mutually-payable set
comparison = compare_clients(G, trials=build_trials(G, 500, 100_000, seed=42, nodes=core))
```

Clients are named `implementation[-variant][@version]` — `lnd-bimodal@0.18.0`,
`eclair-constants-log@0.10.0`, `cln-getroute`. `available_clients()` lists every modelled
release; `PAPER_CLIENTS` is the nine-variant set benchmarked in the paper below.

| | modelled range | variants | search |
|---|---|---|---|
| **LND** | 0.16 – 0.21 | `apriori`, `bimodal`, `uniform` | modified Dijkstra (additive + `attempt_cost / P_path`) |
| **CLN** | 0.10 – 26.04 | `pay`, `getroute` | Dijkstra |
| **LDK** | 0.0.117 – 0.2.x | `default`, `linear` | Dijkstra, cost `max(fee, htlc_min) + penalty` |
| **eclair** | 0.6.2 – 0.14 | `ratios`, `constants`, `constants-log` | Yen's K-shortest, K=3 |

Version boundaries are real behaviour changes, not cosmetic: LDK v0.1.0 turned its live
liquidity penalty **off** by default and swapped its density function; eclair v0.13.1
deleted the `ratios` mode outright; LND v0.19 changed which amount the timelock penalty is
charged against; CLN's formula is unchanged across the whole range (askrene only took over
`pay` in v26.06 and is **not** modelled). `client_profile("lnd")` with no version pins to
the paper's baseline so published results stay reproducible — pass `version="latest"` to
track the newest instead.

The design follows Saraswathi & Kümmerle, [*An Exposition of Pathfinding Strategies Within
Lightning Network Clients*](https://arxiv.org/abs/2410.13784), but **constants are taken
from the upstream sources**, because several of the paper's are wrong — most consequentially
LND's attempt cost, which it gives in msat where the source is in sat (a 1000× error that
sets the entire fee-vs-reliability trade-off). Each module docstring lists the corrections
it applies and cites the file and symbol checked.

What is reproduced is each client's **decision rule on a static snapshot**. Payment
history, learned liquidity bounds, retry loops and multi-part splitting are not — every
success probability is a cold-start estimate, the same assumption the paper's own
simulation makes. See `examples/compare_clients.py` for a one-shot CLI.

### Filtering a snapshot to the nodes that matter

A snapshot is mostly ballast. About half its nodes hold no channel; most of the rest can
never appear *in the middle* of a route. `strong_core` removes them:

```python
from lnhistoryclient.analysis import node_profiles, strong_core

core = strong_core(G, amount_sat=100_000)      # ~9% of nodes, ~58% of channels
profiles = node_profiles(G, amount_sat=100_000)  # per-node features and class
```

A node is **active** when two structural tests pass: it has two *distinct* usable channels
that both admit the amount — one to receive on and a different one to send on — and it sits
in the strongly-connected routable core. Neither is a tuned threshold, and on a 2026
snapshot the nodes passing both carried **100% of the forwarding** in 13 745 simulated
payments while every other node carried none. Filtering is lossless: the same payments
still route, at the same cost.

The quantity behind the first test is `NodeProfile.max_forward_sat`, the largest single
payment the node could forward. It is a min-cut on the node's own star, so it is set by the
**second-largest** usable channel — not the total, and not the largest:

```python
p = profiles[node_id]
p.max_forward_sat   # what it can actually pass
p.capacity_sat      # what it advertises — a node with one 10 BTC channel and nine
                    # 200k channels has 12 BTC of this and forwards 200k
```

The core is **amount-dependent**; recompute it per amount rather than reusing one across a
sweep. `ActivityRule(min_live_channels=N)` tightens the set further, and every positive `N`
is lossy.

## Model
The library provides [python typing models](https://docs.python.org/3/library/typing.html) for every gossip message.
See in the project structure section below for details.

## Project Structure
The function are grouped into different directories depending on their functionality.
On the root level (`.`) the [Lnhistoryrequester.py](./lnhistoryclient/Lnhistoryrequester.py) class is the only class you need to import, in case you want to query the **ln-history platform** and do not have your own data.
The [constants.py](./lnhistoryclient/constants.py) and [common.py](./lnhistoryclient/common.py) files contain constants of the Bitcoin Lightning Specification as well as helper functions.
The [model](./lnhistoryclient/model/) directory [python typing models](https://docs.python.org/3/library/typing.html).
The [parser](./lnhistoryclient/parser/) directory contains all functions to parse a gossip message (including core-lightning internal ones) from raw bytes or hex into something human readable (python typing models). 

## Requirements
Python >=3.9, <4.0

### Dependencies
The core parser has **no runtime dependencies**. Graph/analytics/API features are opt-in
via `pip install "lnhistoryclient[analysis]"`, which pulls
[networkx](https://pypi.org/project/networkx/),
[numpy](https://pypi.org/project/numpy/), [scipy](https://pypi.org/project/scipy/),
[requests](https://pypi.org/project/requests/),
[pandas](https://pypi.org/project/pandas/), and
[matplotlib](https://pypi.org/project/matplotlib/).

## Code Style, Linting etc.
The code has been formatted using [ruff](https://github.com/astral-sh/ruff), [black](https://github.com/psf/black) and [mypy](https://github.com/python/mypy)

## Contributing
Pull requests, issues, and feature ideas are always welcome!
Fork the repo
Create a new branch
Submit a PR with a clear description

This project is MIT licensed.