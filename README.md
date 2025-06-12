# ⚡ ln-history-python-client

A Python client library to **parse and handle raw Lightning Network gossip messages** from the gossip store. Centralized, reusable, and production-tested on real-world data. Perfect for microservices that consume Lightning Network data in `raw_hex` format.

---

## 📦 Features

- 🔍 Parses raw gossip messages: Channel Announcements, Channel Updates, Node Announcements, and more
- 🧱 Clean and extensible object model (e.g., `ChannelAnnouncement`, `NodeAnnouncement`, `ChannelUpdate`)
- 🧪 Tested on real-world data
- 🧰 Built with reusability in mind for microservice architectures

---

## 🛠️ Installation

```bash
pip install 
```

## 🧬 Usage
```python
from lnhistoryclient.parser import parser_factory

raw_hex = "0101..."  # Replace with actual raw gossip hex string
parsed = parser_factory.parse(raw_hex)

print(parsed)
```

You can also directly use individual parsers if you know the message type:

```python
from lnhistoryclient.parser.channel_announcement_parser import parse_channel_announcement

result = parse_channel_announcement(raw_hex)
print(result.channel_id, result.node1_id, result.node2_id)
```

## 📁 Project Structure
```bash
lnhistoryclient
├── model
│   ├── __init__.py
│   ├── Address.py
│   ├── AddressType.py
│   ├── ChannelAnnouncement.py
│   ├── ChannelUpdate.py
│   ├── core-lightning-internal
│   │   ├── __init__.py
│   │   ├── ChannelAmount.py
│   │   ├── ChannelDying.py
│   │   ├── DeleteChannel.py
│   │   ├── GossipStoreEnded.py
│   │   ├── PrivateChannelAnnouncement.py
│   │   └── PrivateChannelUpdate.py
│   └── NodeAnnouncement.py
└── parser
    ├── __init__.py
    ├── channel_announcement_parser.py
    ├── channel_update_parser.py
    ├── common.py
    ├── core-lightning-internal
    │   ├── channel_amount_parser.py
    │   ├── channel_dying_parser.py
    │   ├── delete_channel_parser.py
    │   ├── gossip_store_ended_parser.py
    │   ├── private_channel_announcement_parser.py
    │   └── private_channel_update_parser.py
    ├── node_announcement_parser.py
    └── parser_factory.py
```

## 🧪 Testing
Unit tests coming soon.

## 🧠 Requirements
Python >=3.7, <4.0
Pure Python, no external dependencies

## 🤝 Contributing
Pull requests, issues, and feature ideas are always welcome!
Fork the repo
Create a new branch
Submit a PR with a clear description
