from dataclasses import dataclass
from typing import Any, Dict, Optional

from lnhistoryclient.model.ChannelUpdate import ChannelUpdate
from lnhistoryclient.model.lnd_internal.LNDExtension import LNDExtension


@dataclass
class ExtendedChannelUpdate:
    """
    A wrapper that combines the standard BOLT ChannelUpdate with optional
    LND-specific extensions (like negative inbound fees).
    """

    core: ChannelUpdate
    extension: Optional[LNDExtension] = None

    def __str__(self) -> str:
        """
        Returns a human-readable string combining the core update and extensions.
        Example:
        ExtendedChannelUpdate(core=..., inbound_base=-1500, inbound_ppm=0)
        """
        core_str = str(self.core)

        if self.extension:
            # Create a short summary of the extension
            ext_parts = []
            if self.extension.inbound_fee_base_msat is not None:
                ext_parts.append(f"inbound_base={self.extension.inbound_fee_base_msat}")
            if self.extension.inbound_fee_proportional_millionths is not None:
                ext_parts.append(f"inbound_ppm={self.extension.inbound_fee_proportional_millionths}")
            if self.extension.unknown_tlvs:
                ext_parts.append(f"unknown_tlvs={len(self.extension.unknown_tlvs)}")

            ext_summary = ", ".join(ext_parts)
            return f"ExtendedChannelUpdate(core={core_str}, {ext_summary})"

        return f"ExtendedChannelUpdate(core={core_str})"

    def to_dict(self) -> Dict[str, Any]:
        """
        Returns a flattened dictionary containing all standard fields plus
        any extension fields found.

        Returns:
            Dict[str, Any]: A merged dictionary.
        """
        data = self.core.to_dict()

        if self.extension:
            data.update(self.extension.to_dict())
        else:
            data["inbound_fee_base_msat"] = None
            data["inbound_fee_proportional_millionths"] = None

        return data
