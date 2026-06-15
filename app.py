"""
NEM spot-price dashboard — interactive year/granularity profiles from Postgres.

Two views, both overlaying years on a shared axis:
  * Monthly  — 12 monthly averages per year (Jan..Dec)
  * Diurnal  — 48 half-hourly averages per year (avg price by time of day, AEST)

Profiles for every year are computed in SQL and sent to the browser as one
compact JSON payload; year toggles and the granularity switch are handled
client-side, so interaction needs no server round-trips. Region change reloads.
"""

from __future__ import annotations

import calendar
import json
import os

from flask import Flask, render_template, request

import db

app = Flask(__name__)
DEFAULT_REGION = os.environ.get("REGION", "NSW1")


def build_payload(engine, region: str) -> dict:
    mp = db.monthly_profile(engine, region)
    dp = db.diurnal_profile(engine, region)

    years = sorted({int(y) for y in mp["yr"]} | {int(y) for y in dp["yr"]})
    months = [calendar.month_abbr[m] for m in range(1, 13)]
    times = [f"{t // 60:02d}:{t % 60:02d}" for t in range(0, 1440, 30)]

    monthly: dict[str, list] = {}
    for y in years:
        arr: list = [None] * 12
        for _, r in mp[mp["yr"] == y].iterrows():
            arr[int(r["mth"]) - 1] = round(float(r["mean"]), 2)
        monthly[str(y)] = arr

    diurnal: dict[str, list] = {}
    for y in years:
        arr = [None] * 48
        for _, r in dp[dp["yr"] == y].iterrows():
            idx = int(r["minute_of_day"]) // 30
            if 0 <= idx < 48:
                arr[idx] = round(float(r["mean"]), 2)
        diurnal[str(y)] = arr

    return {
        "region": region,
        "years": years,
        "months": months,
        "times": times,
        "monthly": monthly,
        "diurnal": diurnal,
    }


@app.route("/")
def index():
    engine = db.get_engine()
    if engine is None:
        return render_template("index.html", state="no_db",
                               region=DEFAULT_REGION, regions=db.REGIONS,
                               meta=None, payload=None)

    db.init_db(engine)
    present = db.regions_present(engine)
    if not present:
        return render_template("index.html", state="empty",
                               region=DEFAULT_REGION, regions=db.REGIONS,
                               meta=None, payload=None)

    region = request.args.get("region", DEFAULT_REGION).upper()
    if region not in present:
        region = present[0]

    meta = db.summary(engine, region)
    payload = build_payload(engine, region)
    return render_template("index.html", state="ready", region=region,
                           regions=present, meta=meta,
                           payload=json.dumps(payload))


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
