from dataclasses import dataclass
from typing import Optional

from lnhistoryclient.model.core_lightning_internal.types import GossipStoreEndedDict


@dataclass
class GossipStoreEnded:
    """
    Type 4105: Marks the end of a gossip_store file.

    This message signals that the current gossip store file has been superseded
    (compaction/rotation) and where to resume in its successor.

    Attributes:
        equivalent_offset (int): Offset in the NEW store at which reading resumes.
        uuid (Optional[bytes]): 32-byte generation id of the new store
            (CLN >= v26.06 / gossip_store v16); ``None`` for older stores.
            Readers should compare it against the new store's ``gossip_store_uuid``
            record to detect that a further compaction intervened.
    """

    equivalent_offset: int  # u64
    uuid: Optional[bytes] = None  # 32 bytes since CLN v26.06

    def to_dict(self) -> GossipStoreEndedDict:
        """
        Converts the GossipStoreEnded instance into a strongly-typed dictionary.

        Returns:
            GossipStoreEndedDict: `equivalent_offset` plus the hex `uuid` (or None).
        """
        return {
            "equivalent_offset": self.equivalent_offset,
            "uuid": self.uuid.hex() if self.uuid is not None else None,
        }

    def __str__(self) -> str:
        """
        Returns a string representation of the GossipStoreEnded instance.

        Returns:
            str: A human-readable string showing offset and uuid.
        """
        uuid_repr = self.uuid.hex() if self.uuid is not None else None
        return f"GossipStoreEnded(equivalent_offset={self.equivalent_offset}, uuid={uuid_repr})"
