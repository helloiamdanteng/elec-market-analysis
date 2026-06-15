"""
Build the processed NSW spot-price dataset used by the web app.

Run manually or as Render's build step:

    python build_dataset.py            # last 10 years, NSW1
    python build_dataset.py --years 5 --region QLD1

Outputs (written to ./data):
    raw_<REGION>.parquet       30-minute RRP + demand
    daily_<REGION>.parquet     daily mean / min / max RRP
    monthly_<REGION>.parquet   monthly mean / median RRP + volatility
    annual_<REGION>.parquet    calendar-year mean / median RRP
    meta_<REGION>.json         summary stats for the dashboard header
"""

from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd

from data_fetch import fetch_range, default_window

DATA_DIR = pathlib.Path(__file__).parent / "data"


def build(region: str, years: int) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    start, end = default_window(years)
    print(f"Fetching {region} RRP from {start} to {end} ...")
    raw = fetch_range(start, end, region=region)
    print(f"Done: {len(raw):,} half-hourly intervals "
          f"({raw.index.min():%Y-%m-%d} -> {raw.index.max():%Y-%m-%d})")

    rrp = raw["rrp"]

    daily = pd.DataFrame({
        "mean": rrp.resample("D").mean(),
        "min": rrp.resample("D").min(),
        "max": rrp.resample("D").max(),
    }).dropna()

    monthly = pd.DataFrame({
        "mean": rrp.resample("MS").mean(),
        "median": rrp.resample("MS").median(),
        "p95": rrp.resample("MS").quantile(0.95),
        "std": rrp.resample("MS").std(),
    }).dropna()

    annual = pd.DataFrame({
        "mean": rrp.resample("YS").mean(),
        "median": rrp.resample("YS").median(),
    }).dropna()

    raw.to_parquet(DATA_DIR / f"raw_{region}.parquet")
    daily.to_parquet(DATA_DIR / f"daily_{region}.parquet")
    monthly.to_parquet(DATA_DIR / f"monthly_{region}.parquet")
    annual.to_parquet(DATA_DIR / f"annual_{region}.parquet")

    peak_month = monthly["mean"].idxmax()
    meta = {
        "region": region,
        "start": raw.index.min().strftime("%Y-%m-%d"),
        "end": raw.index.max().strftime("%Y-%m-%d"),
        "intervals": int(len(raw)),
        "mean_all": round(float(rrp.mean()), 2),
        "latest_month": monthly.index.max().strftime("%b %Y"),
        "latest_month_mean": round(float(monthly["mean"].iloc[-1]), 2),
        "peak_month": peak_month.strftime("%b %Y"),
        "peak_month_mean": round(float(monthly["mean"].max()), 2),
        "max_interval": round(float(rrp.max()), 2),
        "min_interval": round(float(rrp.min()), 2),
        "negative_share": round(float((rrp < 0).mean()) * 100, 2),
    }
    with open(DATA_DIR / f"meta_{region}.json", "w") as fh:
        json.dump(meta, fh, indent=2)

    print("Wrote:", ", ".join(p.name for p in sorted(DATA_DIR.glob(f"*_{region}.*"))))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="NSW1")
    ap.add_argument("--years", type=int, default=10)
    args = ap.parse_args()
    build(args.region.upper(), args.years)
