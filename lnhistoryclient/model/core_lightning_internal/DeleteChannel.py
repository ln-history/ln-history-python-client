from dataclasses import dataclass

from lnhistoryclient.model.core_lightning_internal.types import DeleteChannelDict
from lnhistoryclient.parser.common import get_scid_from_int


@dataclass
class DeleteChannel:
    """
    Type 4103: Indicates the deletion of a previously announced channel.

    This custom message is used when a channel is no longer valid and should be
    removed from the gossip store and routing tables.

    Attributes:
        scid (int): The unique 64-bit identifier of the channel to delete.
    """

    scid: int  # u64

    @property
    def scid_str(self) -> str:
        """
        Returns a human-readable representation of the scid
        in the format 'blockheightxtransactionIndexxoutputIndex'.

        Returns:
            str: Formatted string representing the SCID components.
        """
        return get_scid_from_int(self.scid)

    def to_dict(self) -> DeleteChannelDict:
        return {"scid": self.scid_str}

    def __str__(self) -> str:
        return f"DeleteChannel(scid={self.scid_str})"
