from dataclasses import dataclass

from lnhistoryclient.model.types import ChannelUpdateDict
from lnhistoryclient.parser.common import get_scid_from_int


@dataclass
class ChannelUpdate:
    """
    Represents a Lightning Network channel update message.

    This message communicates changes to channel parameters like fees, htlc limits,
    and directional flags between two nodes for an existing channel.

    Attributes:
        signature (bytes): Signature validating the update.
        chain_hash (bytes): Hash of the blockchain genesis block.
        id (int): Unique identifier for the channel.
        timestamp (int): UNIX timestamp when the update was created.
        message_flags (bytes): Flags indicating optional message fields.
        channel_flags (bytes): Flags indicating direction and disabled status.
        cltv_expiry_delta (int): Delta added to the `cltv_expiry` of HTLCs.
        htlc_minimum_msat (int): Minimum value for HTLCs over the channel.
        fee_base_msat (int): Base fee charged for HTLCs (in millisatoshis).
        fee_proportional_millionths (int): Fee rate in millionths of an HTLC.
        htlc_maximum_msat (int | None): Optional max value for HTLCs.
    """

    signature: bytes
    chain_hash: bytes
    scid: int
    timestamp: int
    message_flags: bytes
    channel_flags: bytes
    cltv_expiry_delta: int
    htlc_minimum_msat: int
    fee_base_msat: int
    fee_proportional_millionths: int
    htlc_maximum_msat: int | None = None

    @property
    def scid_str(self) -> str:
        """
        Returns a human-readable representation of the scid
        in the format 'blockheightxtransactionIndexxoutputIndex'.

        Returns:
            str: Formatted string representing the SCID components.
        """
        return get_scid_from_int(self.scid)

    @property
    def direction(self) -> int:
        """
        Returns the direction bit (0 or 1) from the channel_flags byte.

        The direction indicates which node the update is from:
            - 0: Node1 to Node2
            - 1: Node2 to Node1

        Returns:
            int: 0 or 1
        """
        return self.channel_flags[0] & 0x01

    def __str__(self) -> str:
        return (
            f"ChannelUpdate(scid={self.scid_str}, timestamp={self.timestamp}, "
            f"flags=msg:{self.message_flags.hex()}, chan:{self.channel_flags.hex()}, "
            f"cltv_delta={self.cltv_expiry_delta}, min_htlc={self.htlc_minimum_msat}, "
            f"fee_base={self.fee_base_msat}, fee_ppm={self.fee_proportional_millionths}, "
            f"max_htlc={self.htlc_maximum_msat})"
        )

    def to_dict(self) -> ChannelUpdateDict:
        return {
            "signature": self.signature.hex(),
            "chain_hash": self.chain_hash.hex(),
            "scid": self.scid_str,
            "timestamp": self.timestamp,
            "message_flags": self.message_flags.hex(),
            "channel_flags": self.channel_flags.hex(),
            "cltv_expiry_delta": self.cltv_expiry_delta,
            "htlc_minimum_msat": self.htlc_minimum_msat,
            "fee_base_msat": self.fee_base_msat,
            "fee_proportional_millionths": self.fee_proportional_millionths,
            "htlc_maximum_msat": self.htlc_maximum_msat,
        }
