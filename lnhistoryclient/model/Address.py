from model.AddressType import AddressType

class Address:
    """
    Represents a network address with type, IP, and port information.
    """

    def __init__(self):
        """
        Initialize an empty Address.
        """
        self.typ: AddressType = None
        self.addr: str = None
        self.port: int = None

    def __repr__(self):
        """
        Return a string representation of the Address.

        Returns:
            str: A string showing the type, address, and port.
        """
        return f"<Address type={self.typ} addr={self.addr} port={self.port}>"

    def to_dict(self):
        """
        Convert the Address to a dictionary.

        Returns:
            dict: A dictionary representation of the Address.
        """
        return {
            "type": self.typ.to_dict() if self.typ else None,
            "address": self.addr,
            "port": self.port
        }
