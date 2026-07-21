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

with LnhistoryRequester(backend_url="http://localhost:5050") as client:
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