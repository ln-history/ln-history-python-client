import io
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from lnhistoryclient.constants import TLV_TYPE_LND_INBOUND_FEES
from lnhistoryclient.parser.common import varint_decode


@dataclass
class LNDExtension:
    """
    Holds LND-specific extension fields.
    Structure defined in lnd/lnwire/typed_fee.go
    """

    inbound_fee_base_msat: Optional[int] = None
    inbound_fee_proportional_millionths: Optional[int] = None

    unknown_tlvs: Dict[int, bytes] = field(default_factory=dict)

    @classmethod
    def from_stream(cls, stream: io.BytesIO, byte_order: str = "<") -> "LNDExtension":
        """
        Parses LND extensions.

        Args:
            stream: The data stream positioned at the extensions.
            byte_order:
            - '<' for Little Endian,
            - '>' for Big Endian (Wire/BOLT).
        """
        ext = cls()

        while True:
            start_pos = stream.tell()
            if not stream.read(1):
                break
            stream.seek(start_pos)

            tlv_type = varint_decode(stream, big_endian=True)
            tlv_length = varint_decode(stream, big_endian=True)

            current_pos = stream.tell()
            stream.seek(0, io.SEEK_END)
            end_pos = stream.tell()
            stream.seek(current_pos)  # Go back to where we were

            if (end_pos - current_pos) < tlv_length:
                print(f"Problem: Stream has {end_pos - current_pos} bytes, but TLV needs {tlv_length}")
                break

            # 3. Decode Type 55555 (Inbound Fee)
            if tlv_type == TLV_TYPE_LND_INBOUND_FEES:
                # Per typed_fee.go:
                # Field 1: BaseFee (int32)
                # Field 2: FeeRate (int32)
                # Unpack two signed 32-bit integers

                payload = stream.read(tlv_length)

                base = int.from_bytes(payload[:4], byteorder="big", signed=True)
                rate = int.from_bytes(payload[4:8], byteorder="big", signed=True)

                ext.inbound_fee_base_msat = base
                ext.inbound_fee_proportional_millionths = rate

            else:
                value = stream.read(tlv_length)
                ext.unknown_tlvs[tlv_type] = value

        return ext

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "inbound_fee_base_msat": self.inbound_fee_base_msat,
            "inbound_fee_proportional_millionths": self.inbound_fee_proportional_millionths,
        }
        if self.unknown_tlvs:
            data["unknown_tlvs"] = {k: v.hex() for k, v in self.unknown_tlvs.items()}
        return data
