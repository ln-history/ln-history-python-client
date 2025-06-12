from dataclasses import dataclass

@dataclass
class DeleteChannel:
    """
    Type 4103: Indicates the deletion of a previously announced channel.

    This custom message is used when a channel is no longer valid and should be 
    removed from the gossip store and routing tables.

    Attributes:
        short_channel_id (int): The unique 64-bit identifier of the channel to delete.
    """

    short_channel_id: int  # u64

    @property
    def short_channel_id_str(self):
        """
        Returns a human-readable representation of the short_channel_id (scid)
        in the format 'blockxtransactionxoutput'.

        Returns:
            str: Formatted string representing the SCID components.
        """
        block = (self.short_channel_id >> 40) & 0xFFFFFF
        txindex = (self.short_channel_id >> 16) & 0xFFFFFF
        output = self.short_channel_id & 0xFFFF
        return f"{block}x{txindex}x{output}"

    def to_dict(self) -> dict:
        return {
            "short_channel_id": self.short_channel_id_str
        }

    def __str__(self) -> str:
        return f"DeleteChannel(scid={self.short_channel_id_str})"
