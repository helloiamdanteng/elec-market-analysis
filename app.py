"""
NSW electricity spot-price dashboard.

Serves an interactive 10-year view of the NSW Regional Reference Price
(the NEM "pool" price) built from AEMO aggregated price & demand data.

Run locally:
    python build_dataset.py        # fetch + cache data first
    python app.py                  # http://localhost:8000

On Render, build_dataset.py runs as the build step (see render.yaml),
and gunicorn serves this module.
"""

from __future__ import annotations

import json
import os
import pathlib

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, render_template

app = Flask(__name__)

DATA_DIR = pathlib.Path(__file__).parent / "data"
REGION = os.environ.get("REGION", "NSW1")

# Palette — a grid/instrument aesthetic, not a generic dashboard.
INK = "#0E1A2B"
SURFACE = "#16263B"
GRID = "#22344A"
TEXT = "#E6EEF6"
MUTED = "#8DA2B8"
LINE = "#46B3A6"      # monthly mean — teal
BASELINE = "#F2B441"  # annual mean — amber
SPIKE = "#E5675C"     # high-price emphasis


def _load(name: str) -> pd.DataFrame:
    return pd.read_parquet(DATA_DIR / f"{name}_{REGION}.parquet")


def has_data() -> bool:
    return (DATA_DIR / f"monthly_{REGION}.parquet").exists()


def build_figure() -> str:
    monthly = _load("monthly")
    annual = _load("annual")

    fig = go.Figure()

    # Monthly average pool price — the primary series.
    fig.add_trace(go.Scatter(
        x=monthly.index, y=monthly["mean"],
        name="Monthly average",
        mode="lines",
        line=dict(color=LINE, width=2),
        hovertemplate="%{x|%b %Y}<br>$%{y:.0f}/MWh<extra></extra>",
        fill="tozeroy",
        fillcolor="rgba(70,179,166,0.10)",
    ))

    # Annual average — a calmer baseline to read the trend against.
    step_x, step_y = [], []
    for ts, row in annual.iterrows():
        step_x += [ts, ts + pd.offsets.YearEnd(0)]
        step_y += [row["mean"], row["mean"]]
    fig.add_trace(go.Scatter(
        x=step_x, y=step_y,
        name="Annual average",
        mode="lines",
        line=dict(color=BASELINE, width=1.5, dash="dot"),
        hoverinfo="skip",
    ))

    # Emphasise the worst month so the 2022 energy crisis reads at a glance.
    peak_ts = monthly["mean"].idxmax()
    peak_val = float(monthly["mean"].max())
    fig.add_trace(go.Scatter(
        x=[peak_ts], y=[peak_val],
        name="Peak month",
        mode="markers+text",
        marker=dict(color=SPIKE, size=9, line=dict(color=INK, width=1.5)),
        text=[f"  {peak_ts:%b %Y} · ${peak_val:,.0f}"],
        textposition="middle right",
        textfont=dict(color=SPIKE, size=12),
        hoverinfo="skip",
    ))

    fig.update_layout(
        paper_bgcolor=INK,
        plot_bgcolor=SURFACE,
        font=dict(color=TEXT, family="Inter, system-ui, sans-serif"),
        margin=dict(l=64, r=28, t=20, b=48),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED)),
        xaxis=dict(gridcolor=GRID, zeroline=False, color=MUTED),
        yaxis=dict(gridcolor=GRID, zeroline=False, color=MUTED,
                   title="Spot price ($/MWh)", tickprefix="$"),
        height=520,
    )
    return fig.to_json()


@app.route("/")
def index():
    if not has_data():
        return render_template("index.html", ready=False, meta=None, figure=None)
    meta = json.loads((DATA_DIR / f"meta_{REGION}.json").read_text())
    return render_template("index.html", ready=True, meta=meta, figure=build_figure())


@app.route("/healthz")
def healthz():
    return {"ok": True, "data": has_data(), "region": REGION}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
