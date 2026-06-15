"""
Incremental ingest of AEMO price & demand into Postgres.

Strategy: for each region, find the latest settlement already stored (the
high-water mark) and fetch only from that month forward. The current and most
recent month are always re-fetched so late-settling intervals get filled in;
the upsert is idempotent (ON CONFLICT DO NOTHING) so re-fetching is free.

Usage:
    python ingest.py                      # incremental, all regions
    python ingest.py --backfill           # first load: last 10 years, all regions
    python ingest.py --regions NSW1 QLD1  # subset
    python ingest.py --years 5 --backfill

Designed to run as a scheduled job on Render (see render.yaml), decoupled from
deploys — the web service never scrapes.
"""

from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy.engine import Engine

import db
from data_fetch import fetch_range, default_window


def latest_settlement(engine: Engine, region: str) -> pd.Timestamp | None:
    with engine.connect() as conn:
        from sqlalchemy import text
        val = conn.execute(
            text("SELECT max(settlement) FROM price_demand WHERE region = :r"),
            {"r": region},
        ).scalar()
    return pd.Timestamp(val) if val is not None else None


def upsert_price_demand(engine: Engine, region: str, frame: pd.DataFrame) -> int:
    """Idempotently insert a region's rows. Returns rows newly inserted."""
    if frame.empty:
        return 0
    records = [
        (region, idx.to_pydatetime(),
         None if pd.isna(r.rrp) else float(r.rrp),
         None if pd.isna(r.demand_mw) else float(r.demand_mw))
        for idx, r in frame.iterrows()
    ]
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            # RETURNING + fetch=True gives an accurate count across all pages
            # (cur.rowcount alone only reflects the final page).
            returned = execute_values(
                cur,
                """INSERT INTO price_demand (region, settlement, rrp, demand_mw)
                   VALUES %s
                   ON CONFLICT (region, settlement) DO NOTHING
                   RETURNING 1""",
                records,
                page_size=5000,
                fetch=True,
            )
            inserted = len(returned)
        raw.commit()
    finally:
        raw.close()
    return inserted


def current_month() -> str:
    return dt.date.today().strftime("%Y-%m")


def ingest_region(engine: Engine, region: str, years: int, backfill: bool) -> None:
    hwm = None if backfill else latest_settlement(engine, region)
    if hwm is None:
        start, _ = default_window(years)
        print(f"[{region}] no high-water mark -> backfilling from {start}")
    else:
        # Re-fetch from the start of the high-water month to catch stragglers.
        start = hwm.strftime("%Y-%m")
        print(f"[{region}] high-water mark {hwm:%Y-%m-%d %H:%M} -> fetching from {start}")
    end = current_month()

    frame = fetch_range(start, end, region=region, verbose=False)
    inserted = upsert_price_demand(engine, region, frame)
    print(f"[{region}] fetched {len(frame):,} intervals, inserted {inserted:,} new")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", nargs="+", default=db.REGIONS)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--backfill", action="store_true",
                    help="ignore high-water mark and load the full window")
    args = ap.parse_args()

    engine = db.get_engine()
    if engine is None:
        raise SystemExit("DATABASE_URL is not set — cannot ingest.")
    db.init_db(engine)

    for region in (r.upper() for r in args.regions):
        ingest_region(engine, region, args.years, args.backfill)

    print("Done. Regions present:", db.regions_present(engine))


if __name__ == "__main__":
    main()
