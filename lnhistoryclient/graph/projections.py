"""Collapse the canonical :class:`networkx.MultiDiGraph` into simple graphs.

Path-based analytics (betweenness, closeness, routing) do not operate cleanly on a
multigraph, so the canonical lossless graph is projected to:

* :func:`to_undirected_simple` — a simple ``Graph`` for topological centrality, with
  parallel channels between a pair summed into a single ``capacity_sat`` /
  ``num_channels`` edge.
* :func:`to_directed_simple` — a simple ``DiGraph`` for weighted routing. Parallel
  channels in the same direction are collapsed to one edge that keeps the cheapest
  policy and the summed capacity, and is disabled only if **every** parallel is.

Capacity is always expressed in satoshis via :func:`edge_capacity_sat`: the attached
``capacity_sat`` when present, else the ``htlc_maximum_msat`` proxy converted to sat.
"""

from typing import Dict, Optional, Tuple

import networkx as nx


def edge_capacity_sat(attrs: Dict[str, object]) -> Optional[int]:
    """Resolve a directed edge's capacity in satoshis.

    Prefers the enriched on-chain ``capacity_sat``; falls back to the
    ``htlc_maximum_msat`` proxy (msat → sat). Returns ``None`` when neither is known.
    """
    capacity_sat = attrs.get("capacity_sat")
    if isinstance(capacity_sat, int):
        return capacity_sat
    htlc_max = attrs.get("htlc_maximum_msat")
    if isinstance(htlc_max, int) and htlc_max > 0:
        return htlc_max // 1000
    return None


def _copy_node_attrs(src: nx.MultiDiGraph, dst: nx.Graph) -> None:
    """Copy node payloads from the canonical graph into a projection."""
    for node_id, attrs in src.nodes(data=True):
        dst.add_node(node_id, **attrs)


def to_directed_simple(graph: nx.MultiDiGraph) -> nx.DiGraph:
    """Project to a simple ``DiGraph`` for routing / fee analysis.

    Parallel channels in the same direction are aggregated:

    * ``capacity_sat`` — summed over parallels (``None`` if none are known).
    * ``fee_base_msat`` / ``fee_proportional_millionths`` / ``cltv_expiry_delta`` /
      ``htlc_minimum_msat`` — taken from the cheapest parallel (lowest
      ``(fee_base_msat, fee_proportional_millionths)``).
    * ``htlc_maximum_msat`` — the max advertised across parallels.
    * ``disabled`` — ``True`` only if every parallel in that direction is disabled.
    * ``num_parallel`` — number of channels collapsed into the edge.
    """
    result = nx.DiGraph()
    _copy_node_attrs(graph, result)

    grouped: Dict[Tuple[str, str], list] = {}
    for src, dst, attrs in graph.edges(data=True):
        grouped.setdefault((src, dst), []).append(attrs)

    for (src, dst), parallels in grouped.items():
        capacities = [c for c in (edge_capacity_sat(a) for a in parallels) if c is not None]
        htlc_maxes = [a["htlc_maximum_msat"] for a in parallels if isinstance(a.get("htlc_maximum_msat"), int)]

        def _fee_key(a: Dict[str, object]) -> Tuple[int, int]:
            base = a.get("fee_base_msat")
            ppm = a.get("fee_proportional_millionths")
            return (
                base if isinstance(base, int) else 2**63,
                ppm if isinstance(ppm, int) else 2**63,
            )

        cheapest = min(parallels, key=_fee_key)
        result.add_edge(
            src,
            dst,
            scid=cheapest.get("scid"),
            direction=cheapest.get("direction"),
            has_update=any(a.get("has_update") for a in parallels),
            disabled=all(a.get("disabled", False) for a in parallels),
            capacity_sat=sum(capacities) if capacities else None,
            htlc_maximum_msat=max(htlc_maxes) if htlc_maxes else None,
            htlc_minimum_msat=cheapest.get("htlc_minimum_msat"),
            fee_base_msat=cheapest.get("fee_base_msat"),
            fee_proportional_millionths=cheapest.get("fee_proportional_millionths"),
            cltv_expiry_delta=cheapest.get("cltv_expiry_delta"),
            num_parallel=len(parallels),
        )
    return result


def to_undirected_simple(graph: nx.MultiDiGraph) -> nx.Graph:
    """Project to a simple undirected ``Graph`` for topological centrality.

    Each distinct channel (``scid``) between a pair of nodes is counted once. The
    resulting edge carries ``capacity_sat`` (sum over distinct channels) and
    ``num_channels`` (count of distinct channels between the pair).
    """
    result = nx.Graph()
    _copy_node_attrs(graph, result)

    # Aggregate per unordered pair, de-duplicating the two directed edges of a channel.
    per_pair_scids: Dict[Tuple[str, str], Dict[int, Optional[int]]] = {}
    for src, dst, key, attrs in graph.edges(keys=True, data=True):
        pair = (src, dst) if src <= dst else (dst, src)
        scid = key if isinstance(key, int) else attrs.get("scid")
        bucket = per_pair_scids.setdefault(pair, {})
        cap = edge_capacity_sat(attrs)
        # Keep the larger of the two directions' capacity proxies for this scid.
        prev = bucket.get(scid)
        if scid not in bucket:
            bucket[scid] = cap
        elif cap is not None and (prev is None or cap > prev):
            bucket[scid] = cap

    for (a, b), scids in per_pair_scids.items():
        caps = [c for c in scids.values() if c is not None]
        result.add_edge(
            a,
            b,
            num_channels=len(scids),
            capacity_sat=sum(caps) if caps else None,
        )
    return result
