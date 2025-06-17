from typing import TypedDict


class SeenEntry(TypedDict, total=False):
    source: str
    seen_at: int


GossipCache = dict[str, SeenEntry]
