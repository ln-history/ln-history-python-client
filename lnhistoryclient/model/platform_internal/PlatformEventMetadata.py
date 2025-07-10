from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class PlatformEventMetadata:
    type: int
    id: str  # SHA256 hash as hex string of raw_gossip_hex of the PlatformEvent
    timestamp: int

    def __str__(self) -> str:
        return f"PlatformEventMetadata(type={self.type}, id={self.id}, timestamp={self.timestamp})"

    def to_dict(self) -> Dict[str, object]:
        return {
            "type": self.type,
            "id": self.id,
            "timestamp": self.timestamp,
        }
