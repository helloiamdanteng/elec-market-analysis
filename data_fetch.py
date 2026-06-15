"""
Fetch AEMO 'Aggregated price and demand' data.

AEMO publishes one CSV per region per month at a stable, public endpoint:

    {BASE_URL}/PRICE_AND_DEMAND_{YYYYMM}_{REGION}1.csv

e.g. https://aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_201907_NSW1.csv

Each file holds 30-minute interval rows with columns:
    REGION, SETTLEMENTDATE, TOTALDEMAND, RRP, PERIODTYPE

RRP is the Regional Reference Price ($/MWh) — the NEM spot ("pool") price.

The base URL is configurable via the AEMO_BASE_URL env var so that if AEMO
ever moves the endpoint, you can repoint it without touching code.
"""

from __future__ import annotations

import io
import os
import time
import datetime as dt
from typing import Iterable

import pandas as pd
import requests

BASE_URL = os.environ.get(
    "AEMO_BASE_URL",
    "https://aemo.com.au/aemo/data/nem/priceanddemand",
).rstrip("/")

# A browser-like UA avoids the occasional edge-server block on bare clients.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

VALID_REGIONS = {"NSW1", "QLD1", "SA1", "TAS1", "VIC1"}


def _normalise_region(region: str) -> str:
    region = region.upper()
    if region in VALID_REGIONS:
        return region
    if region + "1" in VALID_REGIONS:
        return region + "1"
    raise ValueError(f"Unknown region {region!r}; expected one of {sorted(VALID_REGIONS)}")


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of 'YYYYMM' strings between start and end ('YYYY-MM')."""
    periods = pd.date_range(start=start, end=end, freq="MS")
    return [p.strftime("%Y%m") for p in periods]


def fetch_month(yyyymm: str, region: str, session: requests.Session | None = None,
                timeout: int = 30) -> pd.DataFrame:
    """Download a single monthly file. Returns an empty frame on 404."""
    region = _normalise_region(region)
    url = f"{BASE_URL}/PRICE_AND_DEMAND_{yyyymm}_{region}.csv"
    sess = session or requests.Session()
    resp = sess.get(url, headers=HEADERS, timeout=timeout)
    if resp.status_code == 404:
        return pd.DataFrame()
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    return df


def fetch_range(start: str, end: str, region: str = "NSW1",
                pause: float = 0.3, verbose: bool = True) -> pd.DataFrame:
    """
    Download every monthly file in [start, end] for one region and stitch
    them into a single tidy frame indexed by settlement time.

    start / end are 'YYYY-MM'. The current (incomplete) month is fine to
    include — AEMO returns whatever has settled so far.
    """
    region = _normalise_region(region)
    months = month_range(start, end)
    frames: list[pd.DataFrame] = []
    session = requests.Session()

    for i, m in enumerate(months, 1):
        try:
            df = fetch_month(m, region, session=session)
        except requests.RequestException as exc:
            if verbose:
                print(f"  [{i}/{len(months)}] {m} {region}: request failed ({exc}) — skipped")
            continue
        if df.empty:
            if verbose:
                print(f"  [{i}/{len(months)}] {m} {region}: no file (skipped)")
        else:
            frames.append(df)
            if verbose:
                print(f"  [{i}/{len(months)}] {m} {region}: {len(df):,} rows")
        time.sleep(pause)

    if not frames:
        raise RuntimeError(
            f"No data returned for {region} between {start} and {end}. "
            f"Check AEMO_BASE_URL (currently {BASE_URL!r})."
        )

    data = pd.concat(frames, ignore_index=True)
    data["SETTLEMENTDATE"] = pd.to_datetime(data["SETTLEMENTDATE"])
    data = (
        data.rename(columns={
            "SETTLEMENTDATE": "settlement",
            "TOTALDEMAND": "demand_mw",
            "RRP": "rrp",
        })
        .loc[:, ["settlement", "rrp", "demand_mw"]]
        .drop_duplicates(subset="settlement")
        .sort_values("settlement")
        .set_index("settlement")
    )
    return data


def default_window(years: int = 10) -> tuple[str, str]:
    """('YYYY-MM', 'YYYY-MM') covering the last `years` up to last month."""
    today = dt.date.today()
    end = (today.replace(day=1) - dt.timedelta(days=1))  # last day of prev month
    start = end.replace(year=end.year - years, day=1)
    return start.strftime("%Y-%m"), end.strftime("%Y-%m")
