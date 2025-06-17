from dataclasses import dataclass

from lnhistoryclient.model.core_lightning_internal.types import PrivateChannelAnnouncementDict


@dataclass
class PrivateChannelAnnouncement:
    """
    Type 4104: Represents an announcement for a private channel.

    This message contains the serialized public announcement data and the
    funding amount, allowing analysis of private channels that are not publicly
    advertised on the Lightning Network.

    Attributes:
        amount_sat (int): The funding amount in satoshis.
        announcement (bytes): Raw channel announcement message in bytes.
    """

    amount_sat: int  # u64
    announcement: bytes  # u8[len], len: u16

    def to_dict(self) -> PrivateChannelAnnouncementDict:
        return {"amount_sat": self.amount_sat, "announcement": self.announcement.hex()}

    def __str__(self) -> str:
        return f"PrivateChannel(amount_sat={self.amount_sat}, announcement={self.announcement.hex()[:40]}...)"
