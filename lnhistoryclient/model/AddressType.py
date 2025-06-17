from dataclasses import dataclass, field
from typing import Optional

from lnhistoryclient.model.types import AddressTypeDict


@dataclass
class AddressType:
    """
    Represents the type of a network address used in the Lightning Network.

    Attributes:
        id (Optional[int]): The numeric identifier for the address type.
        name (str): The human-readable name corresponding to the ID.
    """

    id: Optional[int] = None
    name: str = field(init=False)

    def __post_init__(self) -> None:
        """Initializes the `name` attribute based on the given `id`."""
        self.name = self.resolve_name(self.id)

    @staticmethod
    def resolve_name(id: Optional[int]) -> str:
        """
        Resolves the human-readable name for a given address type ID.

        Args:
            id (Optional[int]): The numeric identifier of the address type.

        Returns:
            str: The corresponding name or 'Unknown'.
        """
        mapping = {1: "IPv4", 2: "IPv6", 3: "Torv2", 4: "Torv3", 5: "DNS"}
        return mapping.get(id or 0, "Unknown")

    def __str__(self) -> str:
        """
        Returns a string representation of the AddressType.

        Returns:
            str: Human-readable string of the address type.
        """
        return f"<AddressType id={self.id} name='{self.name}'>"

    def to_dict(self) -> AddressTypeDict:
        """
        Converts the AddressType instance into a dictionary.

        Returns:
            AddressTypeDict: A dictionary with `id` and `name`.

        Raises:
            ValueError: If `id` is None (not allowed by AddressTypeDict).
        """
        if self.id is None:
            raise ValueError("id must not be None to convert to AddressTypeDict")
        return {"id": self.id, "name": self.name}
