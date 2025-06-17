from dataclasses import dataclass

from lnhistoryclient.model.core_lightning_internal.types import PrivateChannelUpdateDict


@dataclass
class PrivateChannelUpdate:
    """
    Type 4102: Contains a private channel update.

    This message includes serialized update data for a private channel, typically
    not shared publicly in the global gossip network.

    Attributes:
        update (bytes): Raw channel update message in bytes.
    """

    update: bytes  # u8[len], len: u16

    def to_dict(self) -> PrivateChannelUpdateDict:
        return {"update": self.update.hex()}

    def __str__(self) -> str:
        return f"PrivateChannelUpdate(update={self.update.hex()[:40]}...)"
