from dataclasses import dataclass, asdict

@dataclass
class GossipStoreEnded:
    """
    Type 4105: Marks the end of a gossip_store file.

    This message signals that the current gossip store file has been fully read.
    Useful when parsing multiple files or log rotation boundaries.

    Attributes:
        equivalent_offset (int): The virtual offset at which the file ends.
    """
    equivalent_offset: int  # u64

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        return f"GossipStoreEnded(equivalent_offset={self.equivalent_offset})"