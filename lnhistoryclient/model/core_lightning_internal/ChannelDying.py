from dataclasses import dataclass

@dataclass
class ChannelDying:
    """
    Type 4106: Indicates that a Lightning Network channel is closing or was force-closed.

    This custom message signals that the funding transaction 
    has been spent and the channel is scheduled for deletion.

    Attributes:
        short_channel_id (int): Unique identifier of the closing channel.
        blockheight (int): Height of the block in which the spend occurred.
    """

    scid: int  # u64 # TODO: RENAME every "short_channel_id" to "scid"
    blockheight: int  # u32 # TODO: REMOVE THIS - NO NEED FOR BLOCKHEIGHT SINCE THIS IS ALREADY THE FIRST PART OF THE scid

    @property
    def short_channel_id_str(self):
        """
        Returns a human-readable representation of the short_channel_id (scid)
        in the format 'blockxtransactionxoutput'.

        Returns:
            str: Formatted string representing the SCID components.
        """
        block = (self.scid >> 40) & 0xFFFFFF
        txindex = (self.scid >> 16) & 0xFFFFFF
        output = self.scid & 0xFFFF
        return f"{block}x{txindex}x{output}"

    def to_dict(self) -> dict:
        return {
            "short_channel_id": self.short_channel_id_str,
            "blockheight": self.blockheight
        }

    def __str__(self) -> str:
        return f"ChannelDying(scid={self.short_channel_id_str}, blockheight={self.blockheight})"