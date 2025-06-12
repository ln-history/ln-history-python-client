from dataclasses import dataclass, asdict

@dataclass
class ChannelAmount:
    """
    Type 4101: Represents the capacity of a public Lightning Network channel.

    This is a custom message that conveys the actual amount of satoshis 
    allocated in the channel's funding transaction.

    Attributes:
        satoshis (int): Total channel capacity in satoshis.
    """
    satoshis: int # u64

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return f"ChannelAmount(satoshis={self.satoshis})"