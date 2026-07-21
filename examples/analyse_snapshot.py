#!/usr/bin/env python3
"""Showcase the analytics: fetch a snapshot, rank nodes, and simulate payments.

Usage:
    python examples/analyse_snapshot.py 2021-06-01
    LN_HISTORY_BACKEND_URL=http://localhost:5050 python examples/analyse_snapshot.py now

Requires:  pip install lnhistoryclient[analysis]
"""

import os
import statistics
import sys
from datetime import datetime, timezone

from lnhistoryclient.analysis import Metric, simulate_random_payments, top_nodes_by
from lnhistoryclient.api import LnhistoryRequester
from lnhistoryclient.graph import graph_stats

BACKEND_URL = os.environ.get("LN_HISTORY_BACKEND_URL", "http://localhost:5050")


def _parse_when(arg: str):
    if arg == "now":
        return "now"
    return datetime.fromisoformat(arg).replace(tzinfo=timezone.utc)


def main() -> None:
    when = _parse_when(sys.argv[1] if len(sys.argv) > 1 else "2021-06-01")

    with LnhistoryRequester(backend_url=BACKEND_URL) as client:
        graph = client.get_snapshot(when, with_updates=True, enrich_capacity=True)

    stats = graph_stats(graph)
    print(
        f"Snapshot @ {when}: {stats['nodes_total']} nodes, {stats['channels']} channels, "
        f"largest component {stats['largest_component_pct']}%"
    )

    print("\nTop 10 by betweenness (unweighted):")
    for r in top_nodes_by(graph, Metric.BETWEENNESS, n=10, k=500, seed=42):
        print(f"  {r.rank:>2}. {r.alias or r.node_id[:20]:<24} bc={r.score:.4f}  channels={r.num_channels}")

    print("\nTop 10 by capacity (weighted strength):")
    for r in top_nodes_by(graph, Metric.STRENGTH, weight="capacity", n=10):
        cap_btc = (r.capacity_sat or 0) / 1e8
        print(f"  {r.rank:>2}. {r.alias or r.node_id[:20]:<24} {cap_btc:.2f} BTC  channels={r.num_channels}")

    print("\nTop 10 by fee-weighted betweenness (routing centrality):")
    for r in top_nodes_by(graph, Metric.BETWEENNESS, weight="fee", n=10, k=500, seed=42):
        print(f"  {r.rank:>2}. {r.alias or r.node_id[:20]:<24} bc={r.score:.4f}")

    for amount in (10_000, 100_000, 1_000_000):
        sim = simulate_random_payments(graph, n=1000, amount_sat=amount, seed=7)
        med_hops = statistics.median(sim.hops) if sim.hops else float("nan")
        med_fee = statistics.median(sim.fees_msat) if sim.fees_msat else float("nan")
        print(
            f"\nPayments of {amount:,} sat (n=1000): success={sim.success_rate:.1%}  "
            f"median hops={med_hops}  median fee={med_fee} msat  reasons={sim.failure_reasons}"
        )


if __name__ == "__main__":
    main()
