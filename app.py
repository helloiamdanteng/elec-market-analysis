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
    ap = db.annual_profile(engine, region)
    mdp = db.month_diurnal_profile(engine, region)

    years = sorted(
        {int(y) for y in mp["yr"]} | {int(y) for y in dp["yr"]}
        | {int(y) for y in ap["yr"]} | {int(y) for y in mdp["yr"]}
    )
    months = [calendar.month_abbr[m] for m in range(1, 13)]
    times = [f"{t // 60:02d}:{t % 60:02d}" for t in range(0, 1440, 30)]

    def to_arrays(df, idx_col, val_col, n, transform):
        res: dict[str, list] = {str(y): [None] * n for y in years}
        for _, r in df.iterrows():
            i = transform(int(r[idx_col]))
            if 0 <= i < n:
                res[str(int(r["yr"]))][i] = round(float(r[val_col]), 2)
        return res

    def annual_map(col):
        return {str(int(r["yr"])): round(float(r[col]), 2) for _, r in ap.iterrows()}

    def md_arrays(col):
        res: dict[str, list] = {str(y): [[None] * 48 for _ in range(12)] for y in years}
        for _, r in mdp.iterrows():
            mo = int(r["mth"]) - 1
            t = int(r["minute_of_day"]) // 30
            if 0 <= mo < 12 and 0 <= t < 48:
                res[str(int(r["yr"]))][mo][t] = round(float(r[col]), 2)
        return res

    return {
        "region": region,
        "years": years,
        "months": months,
        "times": times,
        "annual": annual_map("mean"),
        "annual_capped": annual_map("mean_capped"),
        "monthly": to_arrays(mp, "mth", "mean", 12, lambda m: m - 1),
        "monthly_capped": to_arrays(mp, "mth", "mean_capped", 12, lambda m: m - 1),
        "diurnal": to_arrays(dp, "minute_of_day", "mean", 48, lambda x: x // 30),
        "diurnal_capped": to_arrays(dp, "minute_of_day", "mean_capped", 48, lambda x: x // 30),
        "md": md_arrays("mean"),
        "md_capped": md_arrays("mean_capped"),
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
