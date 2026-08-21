from dataclasses import dataclass

from lnhistoryclient.model.core_lightning_internal.types import GossipStoreUuidDict


@dataclass
class GossipStoreUuid:
    """
    Type 4107: Store generation id (CLN >= v26.06, gossip_store v16).

    Written by gossipd at the front of every gossip_store file. Readers use it
    to detect that a store they reopened after rotation is the generation a
    ``gossip_store_ended`` record pointed into (the ended record echoes this
    uuid); a mismatch means another compaction happened in between.

    This is per-store METADATA, not network gossip — it should not be archived
    as a gossip message.

    Attributes:
        uuid (bytes): 32-byte store generation identifier.
    """

    uuid: bytes

    def to_dict(self) -> GossipStoreUuidDict:
        """
        Converts the GossipStoreUuid instance into a strongly-typed dictionary.

        Returns:
            GossipStoreUuidDict: A dictionary containing the hex-encoded `uuid`.
        """
        return {"uuid": self.uuid.hex()}

    def __str__(self) -> str:
        """
        Returns a string representation of the GossipStoreUuid instance.

        Returns:
            str: A human-readable string showing the `uuid`.
        """
        return f"GossipStoreUuid(uuid={self.uuid.hex()})"
