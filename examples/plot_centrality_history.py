#!/usr/bin/env python3
"""Plot the historical concentration of betweenness centrality across snapshots.

Reads the ``<date>_centrality.csv`` files produced by ``download_snapshots.py`` and
plots Gini / top-percentile concentration and Lorenz-curve evolution. The concentration
maths lives in the library (``lnhistoryclient.analysis.concentration``) — this script is
just I/O and plotting.

Requires:  pip install lnhistoryclient[analysis]  (pulls pandas + matplotlib)
"""

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lnhistoryclient.analysis.concentration import gini, lorenz_xy, top_pct_share

SNAPSHOTS_DIR = Path(__file__).resolve().parent.parent / "snapshots"
PLOTS_DIR = Path(__file__).resolve().parent.parent / "plots"


def load_all_centrality() -> pd.DataFrame:
    frames = []
    for path in sorted(SNAPSHOTS_DIR.glob("*_centrality.csv")):
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(path.stem.replace("_centrality", ""))
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No *_centrality.csv files in {SNAPSHOTS_DIR}; run download_snapshots.py first")
    return pd.concat(frames, ignore_index=True)


def build_time_series(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    for d, grp in df.groupby("date"):
        scores = grp["betweenness_centrality"].to_numpy()
        records.append(
            {
                "date": d,
                "gini": gini(scores),
                "top1_pct": top_pct_share(scores, 1),
                "top5_pct": top_pct_share(scores, 5),
                "top10_pct": top_pct_share(scores, 10),
            }
        )
    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def main() -> None:
    PLOTS_DIR.mkdir(exist_ok=True)
    df = load_all_centrality()
    ts = build_time_series(df)
    print(f"Loaded {df['date'].nunique()} snapshots, {len(df):,} rows")

    # Gini + top-percentile concentration over time.
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(ts["date"], ts["gini"] * 100, marker="o", ms=3, label="Gini")
    for col, label in [("top1_pct", "Top 1%"), ("top5_pct", "Top 5%"), ("top10_pct", "Top 10%")]:
        ax.plot(ts["date"], ts[col] * 100, label=label)
    ax.set_ylabel("%")
    ax.set_title("Betweenness Centrality Concentration over Time")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.legend()
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "concentration.png", dpi=150)
    print(f"  saved -> {PLOTS_DIR / 'concentration.png'}")

    # Lorenz curve evolution.
    dates = sorted(df["date"].unique())
    selected = dates[:: max(1, len(dates) // 6)][:6]
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.plot([0, 1], [0, 1], ls="--", color="gray", label="equality")
    for d in selected:
        scores = df[df["date"] == d]["betweenness_centrality"].to_numpy()
        x, y = lorenz_xy(scores)
        ax.plot(x, y, label=f"{pd.Timestamp(d):%Y-%m} (Gini {gini(scores):.2f})")
    ax.set_xlabel("cumulative share of nodes")
    ax.set_ylabel("cumulative share of BC")
    ax.set_title("Lorenz Curve Evolution")
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "lorenz_evolution.png", dpi=150)
    print(f"  saved -> {PLOTS_DIR / 'lorenz_evolution.png'}")

    latest = ts.iloc[-1]
    print(f"Latest {latest['date']:%Y-%m}: Gini={latest['gini'] * 100:.1f}%  top-1%={latest['top1_pct'] * 100:.0f}%")
    _ = np  # numpy available for further analysis in the notebook/REPL


if __name__ == "__main__":
    main()
