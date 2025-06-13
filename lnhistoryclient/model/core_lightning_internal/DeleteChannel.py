from dataclasses import dataclass

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
    def scid_str(self):
        """
        Returns a human-readable representation of the scid
        in the format 'blockxtransactionxoutput'.

        Returns:
            str: Formatted string representing the scid components.
        """
        block = (self.scid >> 40) & 0xFFFFFF
        txindex = (self.scid >> 16) & 0xFFFFFF
        output = self.scid & 0xFFFF
        return f"{block}x{txindex}x{output}"

    def to_dict(self) -> dict:
        return {
            "scid": self.scid_str
        }

    def __str__(self) -> str:
        return f"DeleteChannel(scid={self.scid_str})"
