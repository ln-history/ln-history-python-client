from dataclasses import dataclass

@dataclass
class ChannelAnnouncement:
    """
    Represents a Lightning Network channel announcement message.

    This message is used to announce a newly created channel and includes
    identifying public keys and cryptographic signatures from both participating nodes.

    Attributes:
        features (bytes): Feature flags applicable to the channel.
        chain_hash (bytes): Hash of the genesis block of the blockchain this channel belongs to.
        scid (int): Unique identifier for the channel derived from funding tx.
        node_id_1 (bytes): Public key of the first node.
        node_id_2 (bytes): Public key of the second node.
        bitcoin_key_1 (bytes): Bitcoin key of the first node.
        bitcoin_key_2 (bytes): Bitcoin key of the second node.
        node_signature_1 (bytes): Signature of node_id_1.
        node_signature_2 (bytes): Signature of node_id_2.
        bitcoin_signature_1 (bytes): Signature of bitcoin_key_1.
        bitcoin_signature_2 (bytes): Signature of bitcoin_key_2.
    """
    
    features: bytes
    chain_hash: bytes
    scid: int
    node_id_1: bytes
    node_id_2: bytes
    bitcoin_key_1: bytes
    bitcoin_key_2: bytes
    node_signature_1: bytes
    node_signature_2: bytes
    bitcoin_signature_1: bytes
    bitcoin_signature_2: bytes

    @property
    def scid_str(self):
        block = (self.scid >> 40) & 0xFFFFFF
        txindex = (self.scid >> 16) & 0xFFFFFF
        output = self.scid & 0xFFFF
        return f"{block}x{txindex}x{output}"

    def __str__(self):
        return f"ChannelAnnouncement(scid={self.scid_str}, node_id_1={self.node_id_1.hex()}, node_id_2={self.node_id_2.hex()}, features={self.features.hex()}, chain_hash={self.chain_hash})"
    
    def to_dict(self) -> dict:
        return {
            "features": self.features.hex(),
            "chain_hash": self.chain_hash.hex(),
            "scid": self.scid_str,
            "node_id_1": self.node_id_1.hex(),
            "node_id_2": self.node_id_2.hex(),
            "bitcoin_key_1": self.bitcoin_key_1.hex(),
            "bitcoin_key_2": self.bitcoin_key_2.hex(),
            "node_signature_1": self.node_signature_1.hex(),
            "node_signature_2": self.node_signature_2.hex(),
            "bitcoin_signature_1": self.bitcoin_signature_1.hex(),
            "bitcoin_signature_2": self.bitcoin_signature_2.hex()
        }
