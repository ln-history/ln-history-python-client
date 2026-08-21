# BOLT #7 types, see https://github.com/lightning/bolts/blob/master/07-routing-gossip.md
MSG_TYPE_CHANNEL_ANNOUNCEMENT = 256
MSG_TYPE_NODE_ANNOUNCEMENT = 257
MSG_TYPE_CHANNEL_UPDATE = 258

# Core Lightning internal messages
MSG_TYPE_CHANNEL_AMOUNT = 4101
MSG_TYPE_PRIVATE_CHANNEL_UPDATE = 4102
MSG_TYPE_DELETE_CHANNEL = 4103
MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT = 4104
MSG_TYPE_GOSSIP_STORE_ENDED = 4105
MSG_TYPE_CHANNEL_DYING = 4106
MSG_TYPE_GOSSIP_STORE_UUID = 4107  # CLN >= v26.06 (gossip_store v16): store generation id

# LND Experimental TLV Types
# Used in channel_update extensions for negative/inbound fees
TLV_TYPE_LND_INBOUND_FEES = 55555  # 0x03F9

# Type name map
GOSSIP_TYPE_NAMES = {
    MSG_TYPE_CHANNEL_ANNOUNCEMENT: "channel_announcement",
    MSG_TYPE_NODE_ANNOUNCEMENT: "node_announcement",
    MSG_TYPE_CHANNEL_UPDATE: "channel_update",
    MSG_TYPE_CHANNEL_AMOUNT: "channel_amount",
    MSG_TYPE_PRIVATE_CHANNEL_UPDATE: "private_update",
    MSG_TYPE_DELETE_CHANNEL: "delete_channel",
    MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT: "private_channel",
    MSG_TYPE_GOSSIP_STORE_ENDED: "ended",
    MSG_TYPE_CHANNEL_DYING: "channel_dying",
    MSG_TYPE_GOSSIP_STORE_UUID: "store_uuid",
}

LIGHTNING_TYPES = {
    MSG_TYPE_CHANNEL_ANNOUNCEMENT,
    MSG_TYPE_NODE_ANNOUNCEMENT,
    MSG_TYPE_CHANNEL_UPDATE,
}

CORE_LIGHTNING_TYPES = {
    MSG_TYPE_CHANNEL_AMOUNT,
    MSG_TYPE_PRIVATE_CHANNEL_UPDATE,
    MSG_TYPE_DELETE_CHANNEL,
    MSG_TYPE_PRIVATE_CHANNEL_ANNOUNCEMENT,
    MSG_TYPE_GOSSIP_STORE_ENDED,
    MSG_TYPE_CHANNEL_DYING,
    MSG_TYPE_GOSSIP_STORE_UUID,
}
# NB: membership here means "recognized and parseable" — NOT "should be published
# or archived". Components with a transport/storage policy (e.g. the ZMQ plugin)
# must define their own explicit allow-set rather than reusing these.

# Header format
HEADER_FORMAT = ">HHII"  # flags(2) + len(2) + crc(4) + timestamp(4)

# gossip_store record flag bits (in the 2-byte flags field of each record header)
GOSSIP_STORE_DELETED_BIT = 0x8000  # record is logically deleted; data bytes are zeroed
GOSSIP_STORE_PRIVATE_BIT = 0x4000  # private channel gossip (not relayed publicly)
GOSSIP_STORE_PUSH_BIT = 0x0800  # message should be pushed to peers; data is valid

ALL_TYPES = set(CORE_LIGHTNING_TYPES) | set(LIGHTNING_TYPES)
