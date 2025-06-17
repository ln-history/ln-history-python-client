from dataclasses import dataclass
from typing import Optional

from lnhistoryclient.model.AddressType import AddressType
from lnhistoryclient.model.types import AddressDict


@dataclass
class Address:
    """
    Represents a network address with type, IP, and port information.

    Attributes:
        typ (Optional[AddressType]): The address type.
        addr (Optional[str]): The address (e.g., IP or hostname).
        port (Optional[int]): The port number.
    """

    typ: Optional[AddressType] = None
    addr: Optional[str] = None
    port: Optional[int] = None

    def __str__(self) -> str:
        """
        Return a string representation of the Address.

        Returns:
            str: A string showing the type, address, and port.
        """
        return f"<Address type={self.typ} addr={self.addr} port={self.port}>"

    def to_dict(self) -> AddressDict:
        """
        Convert the Address to a dictionary.

        Returns:
            AddressDict: A typed dictionary representation of the address.

        Raises:
            ValueError: If any required field is None.
        """
        if self.typ is None or self.addr is None or self.port is None:
            raise ValueError("Cannot convert to AddressDict with missing fields")
        return {
            "typ": self.typ.to_dict(),
            "addr": self.addr,
            "port": self.port,
        }
