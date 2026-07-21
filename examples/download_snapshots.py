#!/usr/bin/env python3
"""Download first-of-month LN snapshots and compute betweenness centrality.

Rewritten to use the ``lnhistoryclient`` library (graph builder + analysis) instead of
reimplementing graph construction. Produces, per snapshot, under ``snapshots/``:

  <date>_stats.json      topology statistics (graph.graph_stats)
  <date>_centrality.csv  node ranking by betweenness (analysis.top_nodes_by)

Requires the analysis extra:  pip install lnhistoryclient[analysis]

Environment:
  LN_HISTORY_API_KEY       optional API key (auth is off on local dev backends)
  LN_HISTORY_BACKEND_URL   backend base URL (default http://localhost:5050)
  START_DATE / END_DATE    YYYY-MM-DD range (default 2019-01-01 .. 2019-06-01)
"""

import csv
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import List

from lnhistoryclient.analysis import Metric, top_nodes_by
from lnhistoryclient.api import LnhistoryRequester, LnhistoryRequesterError
from lnhistoryclient.graph import graph_stats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("download_snapshots")

API_KEY = os.environ.get("LN_HISTORY_API_KEY")
BACKEND_URL = os.environ.get("LN_HISTORY_BACKEND_URL", "http://localhost:5050")
START_DATE = date.fromisoformat(os.environ.get("START_DATE", "2019-01-01"))
END_DATE = date.fromisoformat(os.environ.get("END_DATE", "2019-06-01"))
SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "snapshots"


def first_of_months(start: date, end: date) -> List[date]:
    months, cur = [], start.replace(day=1)
    while cur <= end:
        months.append(cur)
        cur = cur.replace(month=cur.month + 1) if cur.month < 12 else cur.replace(year=cur.year + 1, month=1)
    return months


def main() -> None:
    SNAPSHOTS_DIR.mkdir(exist_ok=True)
    dates = first_of_months(START_DATE, END_DATE)
    log.info("Processing %d snapshots (%s .. %s)", len(dates), dates[0], dates[-1])

    with LnhistoryRequester(api_key=API_KEY, backend_url=BACKEND_URL) as client:
        for d in dates:
            stats_path = SNAPSHOTS_DIR / f"{d.isoformat()}_stats.json"
            csv_path = SNAPSHOTS_DIR / f"{d.isoformat()}_centrality.csv"
            if stats_path.exists() and csv_path.exists():
                log.info("[%s] already done", d)
                continue
            try:
                graph = client.get_snapshot(datetime(d.year, d.month, d.day), with_updates=True)
            except LnhistoryRequesterError as e:
                log.error("[%s] API error: %s", d, e)
                continue

            stats = graph_stats(graph, label=d.isoformat())
            stats_path.write_text(json.dumps(stats, indent=2))
            log.info("[%s] nodes=%s channels=%s", d, stats["nodes_total"], stats["channels"])

            n = graph.number_of_nodes()
            k = max(50, int(0.2 * n)) if n > 250 else None  # approximate BC on large graphs
            ranking = top_nodes_by(graph, Metric.BETWEENNESS, n=n, k=k, seed=42)
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["rank", "node_id", "betweenness_centrality", "alias", "channels", "capacity_sat"])
                for r in ranking:
                    writer.writerow([r.rank, r.node_id, f"{r.score:.8f}", r.alias, r.num_channels, r.capacity_sat])
            log.info("[%s] centrality -> %s", d, csv_path.name)


if __name__ == "__main__":
    main()
