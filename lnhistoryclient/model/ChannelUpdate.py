from dataclasses import dataclass

@dataclass
class ChannelUpdate:
    """
    Represents a Lightning Network channel update message.

    This message communicates changes to channel parameters like fees, htlc limits,
    and directional flags between two nodes for an existing channel.

    Attributes:
        signature (bytes): Signature validating the update.
        chain_hash (bytes): Hash of the blockchain genesis block.
        short_channel_id (int): Unique identifier for the channel.
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
    short_channel_id: int
    timestamp: int
    message_flags: bytes
    channel_flags: bytes
    cltv_expiry_delta: int
    htlc_minimum_msat: int
    fee_base_msat: int
    fee_proportional_millionths: int
    htlc_maximum_msat: int | None = None

    @property
    def short_channel_id_str(self) -> str:
        block = (self.short_channel_id >> 40) & 0xFFFFFF
        txindex = (self.short_channel_id >> 16) & 0xFFFFFF
        output = self.short_channel_id & 0xFFFF
        return f"{block}x{txindex}x{output}"

    def __str__(self) -> str:
        return (f"ChannelUpdate(scid={self.short_channel_id_str}, timestamp={self.timestamp}, "
                f"flags=msg:{self.message_flags}, chan:{self.channel_flags}, "
                f"cltv_delta={self.cltv_expiry_delta}, min_htlc={self.htlc_minimum_msat}, "
                f"fee_base={self.fee_base_msat}, fee_ppm={self.fee_proportional_millionths}, "
                f"max_htlc={self.htlc_maximum_msat})")

    def to_dict(self) -> dict:
        return {
            "signature": self.signature.hex(),
            "chain_hash": self.chain_hash.hex(),
            "short_channel_id": self.short_channel_id_str,
            "timestamp": self.timestamp,
            "message_flags": self.message_flags.hex(),
            "channel_flags": self.channel_flags.hex(),
            "cltv_expiry_delta": self.cltv_expiry_delta,
            "htlc_minimum_msat": self.htlc_minimum_msat,
            "fee_base_msat": self.fee_base_msat,
            "fee_proportional_millionths": self.fee_proportional_millionths,
            "htlc_maximum_msat": self.htlc_maximum_msat
        }
