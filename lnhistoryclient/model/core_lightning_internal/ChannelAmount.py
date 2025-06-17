from dataclasses import dataclass

from lnhistoryclient.model.core_lightning_internal.types import ChannelAmountDict


@dataclass
class ChannelAmount:
    """
    Type 4101: Represents the capacity of a public Lightning Network channel.

    This is a custom message that conveys the actual amount of satoshis
    allocated in the channel's funding transaction.

    Attributes:
        satoshis (int): Total channel capacity in satoshis.
    """

    satoshis: int  # u64

    def to_dict(self) -> ChannelAmountDict:
        """
        Converts the ChannelAmount instance into a strongly-typed dictionary.

        Returns:
            ChannelAmountDict: A dictionary containing the `satoshis` key.
        """
        return {"satoshis": self.satoshis}

    def __str__(self) -> str:
        """
        Returns a string representation of the ChannelAmount instance.

        Returns:
            str: A human-readable string showing the `satoshis` value.
        """
        return f"ChannelAmount(satoshis={self.satoshis})"
