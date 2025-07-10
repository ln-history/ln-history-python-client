from dataclasses import dataclass
from typing import Dict

from lnhistoryclient.model.platform_internal.PlatformEventMetadata import PlatformEventMetadata


@dataclass(frozen=True)
class PlatformEvent:
    metadata: PlatformEventMetadata
    raw_gossip_hex: str

    def __str__(self) -> str:
        return f"PlatformEvent(metadata={self.metadata}, raw_gossip_hex={self.raw_gossip_hex})"

    def to_dict(self) -> Dict[str, object]:
        return {"metadata": self.metadata.to_dict(), "raw_gossip_hex": self.raw_gossip_hex}
