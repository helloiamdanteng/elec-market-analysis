"""
NEM spot-price dashboard — reads curated price/demand from Postgres.

Data is populated by ingest.py (run as a scheduled job on Render). This web
process only queries aggregates; it never scrapes AEMO.

Local:
    export DATABASE_URL=postgresql://user:pass@host:5432/dbname
    python ingest.py --backfill
    python app.py            # http://localhost:8000
"""

from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
from flask import Flask, render_template, request

import db

app = Flask(__name__)
DEFAULT_REGION = os.environ.get("REGION", "NSW1")

# Palette — a grid/instrument aesthetic, not a generic dashboard.
INK, SURFACE, GRID = "#0E1A2B", "#16263B", "#22344A"
TEXT, MUTED = "#E6EEF6", "#8DA2B8"
LINE, BASELINE, SPIKE = "#46B3A6", "#F2B441", "#E5675C"


def build_figure(engine, region: str) -> str:
    monthly = db.monthly_series(engine, region)
    annual = db.annual_series(engine, region)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=monthly.index, y=monthly["mean"], name="Monthly average", mode="lines",
        line=dict(color=LINE, width=2),
        hovertemplate="%{x|%b %Y}<br>$%{y:.0f}/MWh<extra></extra>",
        fill="tozeroy", fillcolor="rgba(70,179,166,0.10)",
    ))

    step_x, step_y = [], []
    for ts, row in annual.iterrows():
        step_x += [ts, ts + pd.offsets.YearEnd(0)]
        step_y += [row["mean"], row["mean"]]
    fig.add_trace(go.Scatter(
        x=step_x, y=step_y, name="Annual average", mode="lines",
        line=dict(color=BASELINE, width=1.5, dash="dot"), hoverinfo="skip",
    ))

    if not monthly.empty:
        peak_ts = monthly["mean"].idxmax()
        peak_val = float(monthly["mean"].max())
        fig.add_trace(go.Scatter(
            x=[peak_ts], y=[peak_val], name="Peak month", mode="markers+text",
            marker=dict(color=SPIKE, size=9, line=dict(color=INK, width=1.5)),
            text=[f"  {peak_ts:%b %Y} · ${peak_val:,.0f}"],
            textposition="middle right", textfont=dict(color=SPIKE, size=12),
            hoverinfo="skip",
        ))

    fig.update_layout(
        paper_bgcolor=INK, plot_bgcolor=SURFACE,
        font=dict(color=TEXT, family="Inter, system-ui, sans-serif"),
        margin=dict(l=64, r=28, t=20, b=48), hovermode="x unified",
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
    engine = db.get_engine()
    if engine is None:
        return render_template("index.html", state="no_db",
                               region=DEFAULT_REGION, regions=db.REGIONS,
                               meta=None, figure=None)

    db.init_db(engine)
    present = db.regions_present(engine)
    if not present:
        return render_template("index.html", state="empty",
                               region=DEFAULT_REGION, regions=db.REGIONS,
                               meta=None, figure=None)

    region = request.args.get("region", DEFAULT_REGION).upper()
    if region not in present:
        region = present[0]

    meta = db.summary(engine, region)
    return render_template("index.html", state="ready", region=region,
                           regions=present, meta=meta,
                           figure=build_figure(engine, region))


@app.route("/healthz")
def healthz():
    engine = db.get_engine()
    return {
        "ok": True,
        "db_configured": engine is not None,
        "regions": db.regions_present(engine) if engine is not None else [],
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=True)
