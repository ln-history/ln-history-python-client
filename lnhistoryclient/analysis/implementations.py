"""Guess which Lightning implementation a node runs, from its advertised feature bits.

WHERE THE HEURISTICS COME FROM
    Alex Myers' impscan (https://github.com/endothermicdev/impscan, BSD-3-Clause,
    commit d2cf119). This is a faithful reimplementation of that plugin's rules so the
    same node fingerprints the same way here and there, plus the pieces impscan does not
    need -- a stable family mapping, and an audit of where the rules disagree with
    themselves.

    impscan's own README is explicit that these rules "are apt to break and should be
    routinely updated". Treat the output as evidence, not identity: nothing here is
    authenticated, and a node can advertise whatever bits it likes.

HOW IT WORKS
    BOLT 9 numbers feature bits in pairs: an even bit means the feature is *required* of
    the peer, the odd bit one above means it is *optional*. Implementations differ in
    which features they advertise and whether they mark them required or optional, and
    that pattern is the fingerprint. A heuristic is a set of constraints over those
    pairs; a node matches if it satisfies all of them.

    The heuristics are NOT mutually exclusive, so order decides the answer -- the first
    match in ``HEURISTICS`` wins, most specific first. The README's own example
    (``800000080a6aa2``) satisfies both ``CLN`` and ``2200`` and is reported as CLN
    purely because CLN is listed earlier.

TWO PLACES WHERE impscan DISAGREES WITH ITSELF
    Reproduced rather than silently corrected, because the point of this module is to
    agree with impscan. ``strict=True`` opts into the corrected reading, and the
    difference between the two is worth reporting rather than hiding.

    1. ``Feature.SET`` ("optional or mandatory") is declared but never tested -- the
       loop in ``Heuristic.test`` has no branch for it -- so it applies **no constraint
       at all**. Four of the twelve heuristics rely on it: CLN, CLN v24.02+, CLN v25.05+
       and Eclair. All four are therefore more permissive than they read.
    2. ``Feature.NOT_OPTIONAL`` tests the even bit, which is what ``NOT_MANDATORY``
       tests; it never looks at the odd bit it is named for. No current heuristic uses
       it, so this one is latent.
"""

from enum import Enum
from typing import Dict, List, Optional, Tuple

#: Source of the rules, recorded so a stored fingerprint can be traced to what produced it.
IMPSCAN_SOURCE = "https://github.com/endothermicdev/impscan@d2cf119"

#: Experimental or not-yet-standard bits, named as impscan names them.
PENDING_FEATURES: Dict[str, int] = {
    "OPTION_WILL_FUND_FOR_FOOD": 30,
    "OPTION_QUIESCE": 34,
    "CLN_WANT_PEER_STORAGE": 40,
    "CLN_PROVIDE_PEER_STORAGE": 42,
    "KEYSEND": 54,
    "OPTION_TRAMPOLINE_ROUTING": 56,
    "OPTION_SPLICE": 60,
    "OPTION_SIMPLIFIED_UPDATE": 106,
    "TRUSTED_SWAP_IN_PROVIDER": 142,
    "TRUSTED_BACKUP_CLIENT": 144,
    "TRUSTED_BACKUP_PROVIDER": 146,
    "OPTION_EXPERIMENTAL_SPLICE": 160,
    "LSPS0_CONFORMANCE": 728,
}

#: Established BOLT 9 bits.
ESTABLISHED_FEATURES: Dict[str, int] = {
    "OPTION_DATA_LOSS_PROTECT": 0,
    "INITIAL_ROUTING_SYNC": 2,
    "OPTION_UPFRONT_SHUTDOWN_SCRIPT": 4,
    "OPT_GOSSIP_QUERIES": 6,
    "VAR_ONION_OPTIN": 8,
    "GOSSIP_QUERIES_EX": 10,
    "OPTION_STATIC_REMOTEKEY": 12,
    "PAYMENT_SECRET": 14,
    "BASIC_MPP": 16,
    "OPTION_SUPPORT_LARGE_CHANNEL": 18,
    "OPTION_ANCHOR_OUTPUTS": 20,
    "OPTION_ANCHORS_ZERO_FEE_HTLC_TX": 22,
    "OPTION_ROUTE_BLINDING": 24,
    "OPTION_SHUTDOWN_ANYSEGWIT": 26,
    "OPT_DUAL_FUND": 28,
    "OPTION_ONION_MESSAGES": 38,
    "OPTION_CHANNEL_TYPE": 44,
    "OPTION_SCID_ALIAS": 46,
    "OPTION_PAYMENT_METADATA": 48,
    "OPT_ZEROCONF": 50,
}

FEATURES: Dict[str, int] = {**PENDING_FEATURES, **ESTABLISHED_FEATURES}


class Feature(Enum):
    """What a heuristic requires of one BOLT 9 feature pair."""

    SET = "optional or mandatory"
    MANDATORY = "mandatory"
    OPTIONAL = "optional"
    NOT_MANDATORY = "not mandatory"
    NOT_OPTIONAL = "not optional"
    NOT_SET = "not set"


def _bit(features: int, position: int) -> bool:
    return (features & (1 << position)) != 0


class Heuristic:
    """A named set of feature-pair constraints."""

    def __init__(self, name: str, **constraints: Feature) -> None:
        self.name = name
        self.constraints = constraints

    def test(self, features: int, strict: bool = False) -> bool:
        """Does this bitfield satisfy every constraint?

        Args:
            features: the advertised bitfield as an integer.
            strict: apply ``SET`` and ``NOT_OPTIONAL`` as their names read. impscan does
                not (see the module docstring), so the default reproduces impscan.
        """
        for name, requirement in self.constraints.items():
            even = FEATURES[name]
            odd = even + 1
            if requirement is Feature.MANDATORY and not _bit(features, even):
                return False
            if requirement is Feature.OPTIONAL and not _bit(features, odd):
                return False
            if requirement is Feature.NOT_MANDATORY and _bit(features, even):
                return False
            if requirement is Feature.NOT_SET and (_bit(features, even) or _bit(features, odd)):
                return False
            if requirement is Feature.NOT_OPTIONAL and _bit(features, odd if strict else even):
                return False
            if requirement is Feature.SET and strict and not (_bit(features, even) or _bit(features, odd)):
                return False
        return True


NO_ANYSEGWIT = Heuristic("No OPTION_SHUTDOWN_ANYSEGWIT", OPTION_SHUTDOWN_ANYSEGWIT=Feature.NOT_SET)

CLN_DF_NEW = Heuristic(
    "CLN v23.02+ Dual-Fund",
    OPT_DUAL_FUND=Feature.OPTIONAL,
    OPTION_ROUTE_BLINDING=Feature.OPTIONAL,
    OPTION_DATA_LOSS_PROTECT=Feature.OPTIONAL,
    OPTION_STATIC_REMOTEKEY=Feature.NOT_MANDATORY,
    GOSSIP_QUERIES_EX=Feature.OPTIONAL,
)

CLN_DF_OLD = Heuristic(
    "CLN Broken Dual-Fund",
    OPT_DUAL_FUND=Feature.OPTIONAL,
    OPTION_DATA_LOSS_PROTECT=Feature.OPTIONAL,
    OPTION_STATIC_REMOTEKEY=Feature.NOT_MANDATORY,
    GOSSIP_QUERIES_EX=Feature.OPTIONAL,
    KEYSEND=Feature.OPTIONAL,
)

CLN_24_02 = Heuristic(
    "CLN v24.02+",
    OPTION_DATA_LOSS_PROTECT=Feature.MANDATORY,
    OPT_GOSSIP_QUERIES=Feature.SET,
    OPTION_STATIC_REMOTEKEY=Feature.SET,
    GOSSIP_QUERIES_EX=Feature.OPTIONAL,
    KEYSEND=Feature.OPTIONAL,
    OPTION_ANCHORS_ZERO_FEE_HTLC_TX=Feature.OPTIONAL,
    OPTION_ROUTE_BLINDING=Feature.OPTIONAL,
    OPTION_WILL_FUND_FOR_FOOD=Feature.NOT_SET,
)

CLN_25_05 = Heuristic(
    "CLN v25.05+",
    OPTION_DATA_LOSS_PROTECT=Feature.MANDATORY,
    OPT_GOSSIP_QUERIES=Feature.SET,
    OPTION_STATIC_REMOTEKEY=Feature.SET,
    GOSSIP_QUERIES_EX=Feature.OPTIONAL,
    KEYSEND=Feature.OPTIONAL,
    OPTION_ANCHORS_ZERO_FEE_HTLC_TX=Feature.OPTIONAL,
    OPTION_ROUTE_BLINDING=Feature.OPTIONAL,
    OPTION_WILL_FUND_FOR_FOOD=Feature.NOT_SET,
    CLN_WANT_PEER_STORAGE=Feature.NOT_SET,
    CLN_PROVIDE_PEER_STORAGE=Feature.OPTIONAL,
)

ECLAIR = Heuristic(
    "Eclair",
    OPTION_DATA_LOSS_PROTECT=Feature.SET,
    OPTION_STATIC_REMOTEKEY=Feature.OPTIONAL,
    OPTION_SUPPORT_LARGE_CHANNEL=Feature.OPTIONAL,
    OPTION_ANCHORS_ZERO_FEE_HTLC_TX=Feature.OPTIONAL,
    OPTION_WILL_FUND_FOR_FOOD=Feature.NOT_SET,
)

LND = Heuristic("LND", OPTION_WILL_FUND_FOR_FOOD=Feature.OPTIONAL, OPTION_DATA_LOSS_PROTECT=Feature.MANDATORY)

CLN = Heuristic(
    "CLN",
    OPTION_DATA_LOSS_PROTECT=Feature.OPTIONAL,
    OPTION_UPFRONT_SHUTDOWN_SCRIPT=Feature.OPTIONAL,
    OPTION_STATIC_REMOTEKEY=Feature.SET,
    GOSSIP_QUERIES_EX=Feature.OPTIONAL,
    KEYSEND=Feature.OPTIONAL,
    OPTION_WILL_FUND_FOR_FOOD=Feature.NOT_SET,
)

LDK = Heuristic(
    "LDK",
    OPTION_DATA_LOSS_PROTECT=Feature.MANDATORY,
    VAR_ONION_OPTIN=Feature.MANDATORY,
    OPTION_STATIC_REMOTEKEY=Feature.MANDATORY,
)

ELECTRUM = Heuristic(
    "Electrum",
    OPTION_DATA_LOSS_PROTECT=Feature.OPTIONAL,
    OPTION_UPFRONT_SHUTDOWN_SCRIPT=Feature.NOT_SET,
    OPT_ZEROCONF=Feature.NOT_SET,
    OPTION_WILL_FUND_FOR_FOOD=Feature.NOT_SET,
)

NLIGHTNING = Heuristic("nlightning", VAR_ONION_OPTIN=Feature.OPTIONAL, INITIAL_ROUTING_SYNC=Feature.MANDATORY)

UNKNOWN_2200 = Heuristic("2200", VAR_ONION_OPTIN=Feature.OPTIONAL, OPTION_STATIC_REMOTEKEY=Feature.OPTIONAL)

#: Order is the algorithm, not presentation: the first match wins, so these run most
#: specific first. Same order as impscan's ``all_heuristics``.
HEURISTICS: List[Heuristic] = [
    NLIGHTNING,
    NO_ANYSEGWIT,
    LND,
    CLN_DF_NEW,
    CLN_DF_OLD,
    CLN_25_05,
    CLN_24_02,
    ECLAIR,
    CLN,
    LDK,
    ELECTRUM,
    UNKNOWN_2200,
]

#: Reported when no heuristic matches.
INDEFINITE = "indef"

#: Heuristic -> implementation family. impscan groups the CLN variants and folds
#: NO_ANYSEGWIT and "2200" into Unknown; both of those are *negative* rules that match on
#: the absence of a feature, so they name no implementation and must not be read as one.
FAMILY: Dict[str, str] = {
    "CLN v23.02+ Dual-Fund": "CLN",
    "CLN Broken Dual-Fund": "CLN",
    "CLN v24.02+": "CLN",
    "CLN v25.05+": "CLN",
    "CLN": "CLN",
    "LND": "LND",
    "LDK": "LDK",
    "Eclair": "Eclair",
    "Electrum": "Electrum",
    "nlightning": "nlightning",
    "No OPTION_SHUTDOWN_ANYSEGWIT": "Unknown",
    "2200": "Unknown",
    INDEFINITE: "Unknown",
}

#: Families that name a real implementation. "Unknown" deliberately does not.
IMPLEMENTATIONS = ("LND", "CLN", "LDK", "Eclair", "Electrum", "nlightning")


def features_to_int(features: object) -> int:
    """Accept the bitfield as bytes, hex, or an int, and return the integer.

    BOLT 9 orders the bitfield big-endian with bit 0 the least significant bit of the
    final byte, which is exactly what ``int.from_bytes(..., "big")`` produces.
    """
    if isinstance(features, int):
        return features
    if isinstance(features, (bytes, bytearray, memoryview)):
        return int.from_bytes(bytes(features), "big") if len(bytes(features)) else 0
    if isinstance(features, str):
        text = features.strip()
        return int(text, 16) if text else 0
    raise TypeError(f"unsupported feature bitfield type: {type(features)!r}")


def fingerprint(features: object, strict: bool = False) -> str:
    """The name of the first heuristic this bitfield matches, or ``"indef"``."""
    value = features_to_int(features)
    for heuristic in HEURISTICS:
        if heuristic.test(value, strict=strict):
            return heuristic.name
    return INDEFINITE


def implementation(features: object, strict: bool = False) -> str:
    """The implementation family, with the negative catch-alls reported as Unknown."""
    return FAMILY.get(fingerprint(features, strict=strict), "Unknown")


def classify(features: object, strict: bool = False) -> Tuple[str, str]:
    """``(heuristic, family)`` in one pass, which is what a caller storing both wants."""
    name = fingerprint(features, strict=strict)
    return name, FAMILY.get(name, "Unknown")


def matching_heuristics(features: object, strict: bool = False) -> List[str]:
    """Every heuristic this bitfield satisfies, in order.

    The heuristics are not mutually exclusive and the reported answer is only the first
    match, so this is how you tell a confident fingerprint from a coincidence.
    """
    value = features_to_int(features)
    return [h.name for h in HEURISTICS if h.test(value, strict=strict)]


def decode(features: object) -> Dict[str, str]:
    """Named features and whether each is required or optional. For reading, not deciding."""
    value = features_to_int(features)
    out: Dict[str, str] = {}
    for name, even in sorted(FEATURES.items(), key=lambda kv: kv[1]):
        if _bit(value, even + 1):
            out[name] = "optional"
        elif _bit(value, even):
            out[name] = "mandatory"
    return out


def unknown_bits(features: object) -> List[int]:
    """Set bits that belong to no feature this module knows about."""
    value = features_to_int(features)
    known = {n for even in FEATURES.values() for n in (even, even + 1)}
    return [bit for bit in range(value.bit_length()) if _bit(value, bit) and bit not in known]


#: lnd carries inbound fees in TLV record 55555 of ``channel_update``'s extra opaque data,
#: added in lnd 0.18. Measured against the archive on 2026-09-04: of the nodes emitting the
#: record, 99.4% fingerprint as LND by the independent feature-bit route -- but only 7.9% of
#: LND nodes emit it. Presence is strong evidence; absence is none at all.
INBOUND_FEE_TLV_IMPLIES = "LND"
INBOUND_FEE_TLV_MIN_VERSION = "0.18"


def implementation_from_inbound_fee_tlv(emits_record: Optional[bool]) -> Optional[str]:
    """``"LND"`` if the node emits TLV 55555, else ``None`` -- never "not LND".

    A one-directional test on purpose. The record is lnd-only in practice, so emitting it
    identifies lnd 0.18 or later; not emitting it says nothing, because most lnd nodes do
    not. Returning None for the negative case keeps callers from reading it as evidence.
    """
    return INBOUND_FEE_TLV_IMPLIES if emits_record else None
