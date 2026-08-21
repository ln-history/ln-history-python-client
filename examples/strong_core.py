#!/usr/bin/env python3
"""Filter a snapshot down to the nodes that can actually carry a payment.

Fetches one snapshot, classifies every node, and reports the strong core — the routing
substrate left once the nodes that can only send and receive are removed. Typically about
9% of the node set, holding closer to 60% of the channels.

A node is *active* when two structural tests pass: two **distinct** usable channels admit
the payment, one to receive on and a different one to send out on, and it sits in the
strongly-connected routable core. Neither is a tuned threshold — see
``lnhistoryclient.analysis.activity.DEFAULT_RULE``.

The core is amount-dependent — a node with two 300 000 sat channels routes 100 000 sat and
not 1 000 000 — so pass the amount you actually care about.

Usage:
    python examples/strong_core.py 2026-07-01
    python examples/strong_core.py now --amount 1000000
    python examples/strong_core.py 2026-07-01 --min-live-channels 4 --top 20
    python examples/strong_core.py 2026-07-01 --save-core core.graphml

Requires:  pip install lnhistoryclient[analysis]
"""

import argparse
import os
from datetime import datetime, timezone

from lnhistoryclient.analysis import (
    ActivityClass,
    ActivityRule,
    node_profiles,
    strong_core,
    summarise,
)
from lnhistoryclient.api import LnhistoryRequester

BACKEND_URL = os.environ.get("LN_HISTORY_BACKEND_URL", "http://localhost:5050")


def parse_when(text: str):
    if text == "now":
        return "now"
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _compact(value: float) -> str:
    for limit, suffix in ((1e8, " BTC"), (1e6, "M"), (1e3, "k")):
        if value >= limit:
            return f"{value / limit:.2f}{suffix}"
    return f"{value:.0f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("when", nargs="?", default="now", help="snapshot instant, or 'now'")
    parser.add_argument("--amount", type=int, default=100_000, help="payment size in sat")
    parser.add_argument(
        "--min-live-channels",
        type=int,
        default=0,
        help="optional tightening: minimum channels usable in both directions. Every "
        "positive value discards nodes that do carry traffic",
    )
    parser.add_argument("--top", type=int, default=10, help="how many core nodes to list")
    parser.add_argument("--save-core", metavar="PATH", help="write the filtered graph as GraphML")
    parser.add_argument("--url", default=BACKEND_URL)
    args = parser.parse_args()

    # Real capacities matter here more than anywhere: the forwarding ceiling is a capacity
    # test, and the htlc_maximum proxy understates it on conservatively configured channels.
    with LnhistoryRequester(backend_url=args.url) as client:
        graph = client.get_snapshot(parse_when(args.when), with_updates=True, enrich_capacity=True)

    rule = ActivityRule(amount_sat=args.amount, min_live_channels=args.min_live_channels)
    profiles = node_profiles(graph, args.amount, rule=rule)
    core = strong_core(graph, args.amount, rule=rule, profiles=profiles)
    counts = summarise(profiles)

    nodes, channels = graph.number_of_nodes(), graph.number_of_edges() // 2
    print(f"snapshot {args.when}: {nodes:,} nodes, {channels:,} channels")
    print(f"rule: {rule.describe()}\n")

    for member in ActivityClass:
        count = counts[member]
        print(f"  {member.value:>9}: {count:>7,}  ({count / max(nodes, 1):>6.2%})")

    print(
        f"\nstrong core: {core.number_of_nodes():,} nodes ({core.number_of_nodes() / max(nodes, 1):.1%}), "
        f"{core.number_of_edges() // 2:,} channels ({core.number_of_edges() / max(graph.number_of_edges(), 1):.1%})"
    )

    active = sorted(
        (profile for profile in profiles.values() if profile.is_active),
        key=lambda profile: profile.max_forward_sat,
        reverse=True,
    )
    if active and args.top:
        print(f"\ntop {min(args.top, len(active))} by forwarding ceiling:")
        print(f"  {'node':<18} {'ceiling':>10} {'capacity':>10} {'chans':>6} {'2-way':>6}  alias")
        for profile in active[: args.top]:
            alias = graph.nodes[profile.node_id].get("alias", "") or ""
            print(
                f"  {profile.node_id[:16]}.. {_compact(profile.max_forward_sat):>10} "
                f"{_compact(profile.capacity_sat):>10} {profile.channels:>6} "
                f"{profile.bidirectional:>6}  {alias[:24]}"
            )

    if args.save_core:
        import networkx as nx

        # GraphML rejects None and list attributes, both of which gossip routinely produces
        # (unpriced directions, node addresses), so flatten before writing.
        clean = nx.MultiDiGraph()
        clean.add_nodes_from(
            (node, {key: value for key, value in attrs.items() if isinstance(value, (str, int, float, bool))})
            for node, attrs in core.nodes(data=True)
        )
        for source, target, key, attrs in core.edges(keys=True, data=True):
            clean.add_edge(
                source,
                target,
                key=key,
                **{k: v for k, v in attrs.items() if isinstance(v, (str, int, float, bool))},
            )
        nx.write_graphml(clean, args.save_core)
        print(f"\nwrote {args.save_core}")


if __name__ == "__main__":
    main()
