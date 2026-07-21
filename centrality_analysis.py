#!/usr/bin/env python3
"""
centrality_analysis.py

Historic analysis of Lightning Network betweenness centrality across all
first-of-month snapshots in snapshots/.

Outputs (written to plots/):
  network_size.png        — nodes + channels growth (full stats history)
  gini_over_time.png      — Gini coefficient of BC distribution per snapshot
  concentration.png       — share of total BC held by top 1 / 5 / 10 % of nodes
  lorenz_evolution.png    — overlaid Lorenz curves for ~yearly snapshots
  top_nodes_heatmap.png   — BC rank of the most persistent top-10 nodes over time
  top_node_bc.png         — BC score trajectories for the top-5 persistent nodes

Usage:
    python centrality_analysis.py
"""

import json
import warnings
from pathlib import Path
from typing import Dict, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

# ── Paths ──────────────────────────────────────────────────────────────────────

SNAPSHOTS_DIR = Path(__file__).parent / "snapshots"
PLOTS_DIR = Path(__file__).parent / "plots"

# ── Style ──────────────────────────────────────────────────────────────────────

BG = "#d5d5d5"
BLUE = "#2b5b84"
ORANGE = "#c76d29"
GREEN = "#2e7d32"
PALETTE = [BLUE, ORANGE, GREEN, "#7b2d8b", "#c62828", "#00796b", "#ad6702"]
LINEWIDTH = 2.2

plt.rcParams.update(
    {
        "font.size": 13,
        "axes.titlesize": 14,
        "axes.labelsize": 13,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "legend.fontsize": 11,
        "figure.facecolor": BG,
        "axes.facecolor": BG,
    }
)

# ── Data loading ───────────────────────────────────────────────────────────────


def load_all_centrality() -> pd.DataFrame:
    """Load all *_centrality.csv files into one long DataFrame."""
    frames = []
    for path in sorted(SNAPSHOTS_DIR.glob("*_centrality.csv")):
        date_str = path.stem.replace("_centrality", "")
        df = pd.read_csv(path)
        df["date"] = pd.to_datetime(date_str)
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No *_centrality.csv files found in {SNAPSHOTS_DIR}")
    return pd.concat(frames, ignore_index=True)


def load_all_stats() -> pd.DataFrame:
    """Load all *_stats.json files into one DataFrame, dropping all-zero rows."""
    rows = []
    for path in sorted(SNAPSHOTS_DIR.glob("*_stats.json")):
        with open(path) as f:
            rows.append(json.load(f))
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df[df["nodes_total"] > 0].reset_index(drop=True)


# ── Statistical helpers ────────────────────────────────────────────────────────


def gini(scores: np.ndarray) -> float:
    s = np.sort(scores)
    n = len(s)
    if n == 0 or s.sum() == 0:
        return 0.0
    idx = np.arange(1, n + 1)
    return float((2 * idx - n - 1).dot(s) / (n * s.sum()))


def lorenz_xy(scores: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    s = np.sort(scores)
    cum = np.cumsum(s)
    x = np.insert(np.arange(1, len(s) + 1) / len(s), 0, 0.0)
    y = np.insert(cum / cum[-1] if cum[-1] > 0 else cum, 0, 0.0)
    return x, y


def top_pct_share(scores: np.ndarray, pct: float) -> float:
    """Fraction of total BC held by the top `pct`% of nodes."""
    if scores.sum() == 0:
        return 0.0
    k = max(1, int(np.ceil(len(scores) * pct / 100)))
    return float(np.sort(scores)[-k:].sum() / scores.sum())


# ── Per-snapshot aggregation ───────────────────────────────────────────────────


def build_time_series(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    for date, grp in df.groupby("date"):
        sc = grp["betweenness_centrality"].values
        records.append(
            {
                "date": date,
                "gini": gini(sc),
                "top1_pct": top_pct_share(sc, 1),
                "top5_pct": top_pct_share(sc, 5),
                "top10_pct": top_pct_share(sc, 10),
            }
        )
    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def build_node_rank_pivot(df: pd.DataFrame, n_persistent: int = 12) -> pd.DataFrame:
    """
    Build a (date × node) pivot table of BC rank.
    Only keeps the n_persistent nodes with the highest cumulative appearances in top-10.
    """
    top10 = df[df["rank"] <= 10]
    freq = top10.groupby("node_id").size().nlargest(n_persistent)
    nodes = freq.index.tolist()

    pivot_rows = []
    for date, grp in df.groupby("date"):
        row = {"date": date}
        for nid in nodes:
            match = grp[grp["node_id"] == nid]
            row[nid] = int(match["rank"].iloc[0]) if len(match) else np.nan
        pivot_rows.append(row)

    pivot = pd.DataFrame(pivot_rows).set_index("date").sort_index()

    # Build display labels: alias (truncated) + short key
    labels = {}
    for nid in nodes:
        alias = df[df["node_id"] == nid]["alias"].dropna().iloc[0] if len(df[df["node_id"] == nid]) else ""
        alias = alias[:22] if alias else nid[:14]
        labels[nid] = alias
    pivot.columns = [labels[c] for c in pivot.columns]
    return pivot


# ── Plot functions ─────────────────────────────────────────────────────────────


def _savefig(fig: plt.Figure, name: str) -> None:
    PLOTS_DIR.mkdir(exist_ok=True)
    path = PLOTS_DIR / name
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved → {path}")


def plot_network_size(stats: pd.DataFrame) -> None:
    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax2 = ax1.twinx()
    ax2.set_facecolor(BG)

    ax1.plot(stats["date"], stats["nodes_total"], color=BLUE, lw=LINEWIDTH, label="Nodes")
    ax1.plot(stats["date"], stats["nodes_isolated"], color=BLUE, lw=1.2, ls="--", alpha=0.6, label="Isolated nodes")
    ax2.plot(stats["date"], stats["channels"], color=ORANGE, lw=LINEWIDTH, label="Channels")

    ax1.set_ylabel("Nodes", color=BLUE)
    ax2.set_ylabel("Channels", color=ORANGE)
    ax1.tick_params(axis="y", colors=BLUE)
    ax2.tick_params(axis="y", colors=ORANGE)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha="right")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    ax1.set_title("Lightning Network Growth — Nodes & Channels")
    fig.tight_layout()
    _savefig(fig, "network_size.png")


def plot_gini_over_time(ts: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(ts["date"], ts["gini"] * 100, color=BLUE, lw=LINEWIDTH, marker="o", ms=3)
    ax.set_ylabel("Gini coefficient (%)")
    ax.set_ylim(bottom=max(0, ts["gini"].min() * 100 - 5))
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f%%"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax.set_title("Betweenness Centrality Concentration — Gini Coefficient over Time")
    fig.tight_layout()
    _savefig(fig, "gini_over_time.png")


def plot_concentration(ts: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))
    for col, label, color in [
        ("top1_pct", "Top 1% of nodes", BLUE),
        ("top5_pct", "Top 5% of nodes", ORANGE),
        ("top10_pct", "Top 10% of nodes", GREEN),
    ]:
        ax.plot(ts["date"], ts[col] * 100, color=color, lw=LINEWIDTH, label=label)

    ax.set_ylabel("Share of total betweenness centrality (%)")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax.legend()
    ax.set_title("BC Concentration — Share Held by Top Percentiles over Time")
    fig.tight_layout()
    _savefig(fig, "concentration.png")


def plot_lorenz_evolution(df: pd.DataFrame, n_curves: int = 7) -> None:
    dates = sorted(df["date"].unique())
    step = max(1, len(dates) // n_curves)
    selected = dates[::step][:n_curves]
    cmap = plt.cm.Blues(np.linspace(0.35, 0.95, len(selected)))

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.plot([0, 1], [0, 1], lw=1.5, color="gray", ls="--", label="Line of equality")

    for color, date in zip(cmap, selected, strict=False):
        sc = df[df["date"] == date]["betweenness_centrality"].values
        x, y = lorenz_xy(sc)
        g = gini(sc)
        ax.plot(x, y, lw=1.8, color=color, label=f"{date.strftime('%Y-%m')}  (Gini {g:.2f})")

    ax.set_xlabel("Cumulative share of nodes")
    ax.set_ylabel("Cumulative share of betweenness centrality")
    ax.set_title("Lorenz Curve Evolution")
    ax.legend(loc="upper left", fontsize=10)
    fig.tight_layout()
    _savefig(fig, "lorenz_evolution.png")


def plot_top_nodes_heatmap(df: pd.DataFrame, n_persistent: int = 12) -> None:
    pivot = build_node_rank_pivot(df, n_persistent)

    fig, ax = plt.subplots(figsize=(max(14, len(pivot) * 0.25), 5))

    # Mask NaN (node not in snapshot) — show as white
    data = pivot.values.astype(float)
    masked = np.ma.masked_invalid(data)

    # Colormap: low rank (1 = top) → dark, high rank → light
    cmap = plt.cm.YlOrRd_r
    cmap.set_bad(color="white")
    im = ax.imshow(masked.T, aspect="auto", cmap=cmap, vmin=1, vmax=50, interpolation="nearest")

    ax.set_xticks(range(len(pivot.index)))
    ax.set_xticklabels([d.strftime("%Y-%m") for d in pivot.index], rotation=90, fontsize=8)
    ax.set_yticks(range(len(pivot.columns)))
    ax.set_yticklabels(pivot.columns, fontsize=10)
    ax.set_xlabel("Snapshot")
    ax.set_title("BC Rank of Most Persistent Top-10 Nodes (dark = high rank, white = absent)")

    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.01)
    cbar.set_label("Rank")
    cbar.ax.invert_yaxis()
    fig.tight_layout()
    _savefig(fig, "top_nodes_heatmap.png")


def plot_top_node_bc(df: pd.DataFrame, n_nodes: int = 5) -> None:
    top10 = df[df["rank"] <= 10]
    freq = top10.groupby("node_id").size().nlargest(n_nodes)
    nodes = freq.index.tolist()

    node_labels: Dict[str, str] = {}
    for nid in nodes:
        alias = df[df["node_id"] == nid]["alias"].dropna().values
        node_labels[nid] = alias[0][:24] if len(alias) and alias[0] else nid[:16]

    fig, ax = plt.subplots(figsize=(12, 5))
    for color, nid in zip(PALETTE, nodes, strict=False):
        sub = df[df["node_id"] == nid].sort_values("date")
        ax.plot(
            sub["date"],
            sub["betweenness_centrality"],
            color=color,
            lw=LINEWIDTH,
            marker="o",
            ms=3,
            label=node_labels[nid],
        )

    ax.set_ylabel("Betweenness centrality (normalised)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 7]))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax.legend(loc="upper right")
    ax.set_title("Betweenness Centrality Score — Top-5 Most Persistent Nodes")
    fig.tight_layout()
    _savefig(fig, "top_node_bc.png")


# ── Main ───────────────────────────────────────────────────────────────────────


def main() -> None:
    print("Loading centrality CSVs …")
    df = load_all_centrality()
    print(
        f"  {df['date'].nunique()} snapshots  |  {len(df):,} rows  "
        f"({df['date'].min().strftime('%Y-%m')} → {df['date'].max().strftime('%Y-%m')})"
    )

    print("Loading stats JSONs …")
    stats = load_all_stats()
    print(f"  {len(stats)} non-empty stats snapshots")

    print("Building time-series metrics …")
    ts = build_time_series(df)

    print("Generating plots …")
    plot_network_size(stats)
    plot_gini_over_time(ts)
    plot_concentration(ts)
    plot_lorenz_evolution(df)
    plot_top_nodes_heatmap(df)
    plot_top_node_bc(df)

    print("\nSummary")
    print(f"  Gini range : {ts['gini'].min()*100:.1f}% → {ts['gini'].max()*100:.1f}%")
    print(f"  Top-1% share range : {ts['top1_pct'].min()*100:.0f}% → {ts['top1_pct'].max()*100:.0f}%")
    latest = ts.iloc[-1]
    print(
        f"  Latest ({latest['date'].strftime('%Y-%m')}) : "
        f"Gini={latest['gini']*100:.1f}%  top-1%={latest['top1_pct']*100:.0f}%  "
        f"top-10%={latest['top10_pct']*100:.0f}%"
    )
    print("Done.")


if __name__ == "__main__":
    main()
