from dataclasses import dataclass

from lnhistoryclient.model.core_lightning_internal.types import GossipStoreEndedDict


@dataclass
class GossipStoreEnded:
    """
    Type 4105: Marks the end of a gossip_store file.

    This message signals that the current gossip store file has been fully read.
    Useful when parsing multiple files or identifying log rotation boundaries.

    Attributes:
        equivalent_offset (int): The virtual offset at which the file ends.
    """

    equivalent_offset: int  # u64

    def to_dict(self) -> GossipStoreEndedDict:
        """
        Converts the GossipStoreEnded instance into a strongly-typed dictionary.

        Returns:
            GossipStoreEndedDict: A dictionary containing the `equivalent_offset` key.
        """
        return {"equivalent_offset": self.equivalent_offset}

    def __str__(self) -> str:
        """
        Returns a string representation of the GossipStoreEnded instance.

        Returns:
            str: A human-readable string showing the `equivalent_offset`.
        """
        return f"GossipStoreEnded(equivalent_offset={self.equivalent_offset})"
