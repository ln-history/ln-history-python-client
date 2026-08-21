#!/usr/bin/env python3
"""Compare how LND, CLN, LDK and eclair would each route the same payments.

Fetches one snapshot, draws a set of random payments, and routes every one of them with
every client — the same trials for each, so the differences are pathfinding differences
and nothing else.

Usage:
    python examples/compare_clients.py 2021-06-01
    python examples/compare_clients.py 2021-06-01 --amount 1000000 --trials 200
    python examples/compare_clients.py now --clients lnd@0.17.4 lnd@0.19.0 cln ldk@0.1.0

Requires:  pip install lnhistoryclient[analysis]
"""

import argparse
import os
import sys
from datetime import datetime, timezone

from lnhistoryclient.analysis import PAPER_CLIENTS, available_clients, client_profile, compare_clients
from lnhistoryclient.api import LnhistoryRequester

BACKEND_URL = os.environ.get("LN_HISTORY_BACKEND_URL", "http://localhost:5050")


def parse_when(text: str):
    if text == "now":
        return "now"
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("when", nargs="?", default="2021-06-01", help="snapshot instant, or 'now'")
    parser.add_argument("--amount", type=int, default=100_000, help="payment size in sat")
    parser.add_argument("--trials", type=int, default=200, help="number of payments")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--clients", nargs="+", default=list(PAPER_CLIENTS), help="client specs to compare")
    parser.add_argument("--list-clients", action="store_true", help="print every modelled release and exit")
    parser.add_argument("--url", default=BACKEND_URL)
    args = parser.parse_args()

    if args.list_clients:
        for row in available_clients():
            marker = " (paper baseline)" if row["is_paper_baseline"] else ""
            print(f"{row['implementation']:8} >= {row['since']:9} -> {row['version']:9}{marker}")
            print(f"         variants: {', '.join(row['variants'])} (default: {row['default_variant']})")
            print(f"         {row['notes']}\n")
        return

    # Capacity matters: every client but CLN's getroute variant prices it, so a snapshot
    # without real on-chain capacities would distort the comparison.
    with LnhistoryRequester(backend_url=args.url) as client:
        graph = client.get_snapshot(parse_when(args.when), with_updates=True, enrich_capacity=True)

    print(f"snapshot {args.when}: {graph.number_of_nodes()} nodes, {graph.number_of_edges() // 2} channels")
    print(f"routing {args.trials} payments of {args.amount:,} sat through {len(args.clients)} clients\n")

    comparison = compare_clients(graph, args.clients, n=args.trials, amount_sat=args.amount, seed=args.seed)

    header = f"{'client':30} {'success':>8} {'fee %':>9} {'hops':>6} {'timelock':>9}"
    print(header)
    print("-" * len(header))
    for row in comparison.summary():
        fee = "—" if row["fee_ratio_pct"] is None else f"{row['fee_ratio_pct']:.4f}"
        hops = "—" if row["avg_path_length"] is None else f"{row['avg_path_length']:.2f}"
        cltv = "—" if row["avg_timelock"] is None else f"{row['avg_timelock']:.1f}"
        print(f"{row['client']:30} {row['success_rate']:>7.1%} {fee:>9} {hops:>6} {cltv:>9}")

    print(
        f"\nSuccess rate is over all {comparison.num_trials} trials; fee, hops and timelock "
        f"are over the {len(comparison.common_success)} trials every client routed."
    )
    if not comparison.common_success:
        print("No trial succeeded for every client — only the success column is meaningful.")

    print("\nWhat each profile actually models:")
    for spec in args.clients:
        profile = client_profile(spec)
        print(f"  {profile.label:30} {profile.algorithm.value}")
        if profile.notes:
            print(f"      {profile.notes}")

    # Failure attribution differs between clients too: a client whose failures are mostly
    # 'no_route' is being blocked by its own side constraints, not by the topology.
    print("\nFailure reasons:")
    for outcome in comparison.outcomes:
        if outcome.failure_reasons:
            reasons = ", ".join(f"{name}={count}" for name, count in sorted(outcome.failure_reasons.items()))
            print(f"  {outcome.label:30} {reasons}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
