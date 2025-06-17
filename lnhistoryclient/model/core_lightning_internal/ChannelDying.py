from dataclasses import dataclass

from lnhistoryclient.model.core_lightning_internal.types import ChannelDyingDict
from lnhistoryclient.parser.common import get_scid_from_int


@dataclass
class ChannelDying:
    """
    Type 4106: Indicates that a Lightning Network channel is closing or was force-closed.

    This custom message signals that the funding transaction
    has been spent and the channel is scheduled for deletion.

    Attributes:
        scid (int): Unique identifier of the closing channel.
        blockheight (int): Height of the block in which the spend occurred.
    """

    scid: int  # u64
    blockheight: int

    @property
    def scid_str(self) -> str:
        """
        Returns a human-readable representation of the scid
        in the format 'blockheightxtransactionIndexxoutputIndex'.

        Returns:
            str: Formatted string representing the SCID components.
        """
        return get_scid_from_int(self.scid)

    def to_dict(self) -> ChannelDyingDict:
        return {"scid": self.scid_str, "blockheight": self.blockheight}

    def __str__(self) -> str:
        return f"ChannelDying(scid={self.scid_str})"
