class AddressType:
    """
    Represents the type of a network address used in the Lightning Network.

    This class maps numeric identifiers to human-readable address type names,
    such as IPv4, IPv6, or Tor. It provides a string representation and 
    dictionary export for easy display and serialization.

    Attributes:
        id (int | None): The numeric identifier for the address type.
        name (str): The human-readable name corresponding to the ID.
    """
    
    def __init__(self, id=None):
        self.id = id
        self.name = self.resolve_name(id)

    def resolve_name(self, id):
        mapping = {
            1: "IPv4",
            2: "IPv6",
            3: "Torv2",
            4: "Torv3",
            5: "DNS"
        }
        return mapping.get(id, "Unknown")

    def __repr__(self):
        return f"<AddressType id={self.id} name='{self.name}'>"

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name
        }