from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class PlatformEventMetadata:
    type: int
    id: bytes  # SHA256 hash of raw_gossip_bytes of the PlatformEvent
    timestamp: int

    def __str__(self) -> str:
        return f"PlatformEventMetadata(type={self.type}, id={self.id.hex()}, timestamp={self.timestamp})"

    def to_dict(self) -> Dict[str, object]:
        return {
            "type": self.type,
            "id": self.id.hex(),  # represent bytes as hex string
            "timestamp": self.timestamp,
        }
