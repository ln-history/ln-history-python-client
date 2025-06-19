[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Checked with mypy](https://img.shields.io/badge/type%20checked-mypy-blue)](http://mypy-lang.org/)
![Uses: dataclasses](https://img.shields.io/badge/uses-dataclasses-brightgreen)
![Uses: typing](https://img.shields.io/badge/uses-typing-blue)

[![Commitizen friendly](https://img.shields.io/badge/commitizen-friendly-brightgreen.svg)](http://commitizen.github.io/cz-cli/)

# ⚡ lnhistoryclient

A Python client library to **parse and handle raw Lightning Network gossip messages** from the gossip store. Centralized, reusable, and production-tested on real-world data. Perfect for microservices that consume Lightning Network data in `raw_hex` format.
For details about the gossip messages see the Lightning Network specifications [BOLT #7](https://github.com/lightning/bolts/blob/master/07-routing-gossip.md)
This python package is part of the [ln-history project](https://github.com/ln-history)

---

## 📦 Features

- 🔍 Parses raw gossip messages: `ChannelAnnouncement`, `NodeAnnouncement`, `ChannelUpdate`, and more
- 🧱 Clean and extensible object model (e.g., `ChannelAnnouncement`, `NodeAnnouncement`, `ChannelUpdate`)
- 🧪 Tested on real-world data
- 🧰 Built with reusability in mind for microservice architectures

---

## 🛠️ Installation

```bash
pip install lnhistoryclient
```

## 🧬 Usage

To parse a raw Lightning Network gossip message, first extract the message type,
then use the type to select the appropriate parser. This ensures correctness
and avoids interpreting invalid data.
The library accepts both bytes and io.BytesIO objects as input for maximum flexibility.

```python
from lnhistoryclient.parser.common import get_message_type
from lnhistoryclient.parser.parser_factory import get_parser_by_message_type


raw_hex = bytes.fromhex("0101...")  # Replace with actual raw hex (includes 2-byte type prefix)

msg_type = get_message_type_by_raw_hex(raw_hex)
if msg_type is not None:
    parser = get_parser_by_message_type(msg_type)
    result = parser(raw_hex[2:])  # Strip the type prefix if your parser expects it
    print(result)
else:
    print("Unknown or unsupported message type.")
```

For convenience (and if you're confident the input is valid), a shortcut is also available:

```python
from lnhistoryclient.parser.parser_factory import get_parser_from_raw_hex

raw_hex = bytes.fromhex("0101...")  # Replace with actual raw hex

parser = get_parser_from_raw_hex(raw_hex)
if parser:
    result = parser(raw_hex[2:])
    print(result)
else:
    print("Could not determine parser.")
```

You can also directly use individual parsers if you know the message type:

```python
from lnhistoryclient.parser.channel_announcement_parser import parse_channel_announcement

result = parse_channel_announcement(raw_hex)
print(result.channel_id, result.node1_id, result.node2_id)
```

## 🎨 Model
The library provides [python typing models](https://docs.python.org/3/library/typing.html) for every gossip message.
See in the project structure section below for details.

## 📁 Project Structure
```bash
lnhistoryclient
├── LICENSE
├── lnhistoryclient
│   ├── constants.py
│   ├── model
│   │   ├── __init__.py
│   │   ├── Address.py
│   │   ├── AddressType.py
│   │   ├── cache
│   │   │   └── GossipCache.py
│   │   ├── ChannelAnnouncement.py
│   │   ├── ChannelUpdate.py
│   │   ├── core_lightning_internal
│   │   │   ├── __init__.py
│   │   │   ├── ChannelAmount.py
│   │   │   ├── ChannelDying.py
│   │   │   ├── DeleteChannel.py
│   │   │   ├── GossipStoreEnded.py
│   │   │   ├── PrivateChannelAnnouncement.py
│   │   │   ├── PrivateChannelUpdate.py
│   │   │   └── types.py
│   │   ├── gossip_event_kafka
│   │   │   ├── __init__.py
│   │   │   └── GossipEvent.py
│   │   ├── gossip_event_zmq
│   │   │   ├── __init__.py
│   │   │   ├── ChannelAnnouncementEvent.py
│   │   │   ├── ChannelUpdateEvent.py
│   │   │   ├── core_lightning_internal
│   │   │   │   ├── __init__.py
│   │   │   │   ├── ChannelAmountEvent.py
│   │   │   │   ├── ChannelDyingEvent.py
│   │   │   │   ├── DeleteChannelEvent.py
│   │   │   │   ├── GossipStoreEndedEvent.py
│   │   │   │   ├── PrivateChannelAnnouncementEvent.py
│   │   │   │   └── PrivateChannelUpdateEvent.py
│   │   │   └── NodeAnnouncementEvent.py
│   │   ├── MessageMetadata.py
│   │   ├── NodeAnnouncement.py
│   │   └── types.py
│   └── parser
│       ├── __init__.py
│       ├── channel_announcement_parser.py
│       ├── channel_update_parser.py
│       ├── common.py
│       ├── core_lightning_internal
│       │   ├── __init__.py
│       │   ├── channel_amount_parser.py
│       │   ├── channel_dying_parser.py
│       │   ├── delete_channel_parser.py
│       │   ├── gossip_store_ended_parser.py
│       │   ├── private_channel_announcement_parser.py
│       │   └── private_channel_update_parser.py
│       ├── node_announcement_parser.py
│       └── parser_factory.py
├── pyproject.toml
├── README.md
├── requirements-dev.txt
└── tests
```

## 🧪 Testing
Unit tests coming soon.

## 🧠 Requirements
Python >=3.7, <4.0
Pure Python, no external dependencies

## Code Style, Linting etc.
The code has been formatted using [ruff](https://github.com/astral-sh/ruff), [black]() and [mypy]()

## 🤝 Contributing
Pull requests, issues, and feature ideas are always welcome!
Fork the repo
Create a new branch
Submit a PR with a clear description
