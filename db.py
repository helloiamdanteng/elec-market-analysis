"""
Database layer for the electricity market store (Postgres).

Tier 1 (here): curated half-hourly price & demand for all NEM regions, kept
in Postgres and queried by the dashboard. Small, append-only, fast.

Tier 2 (later): raw 5-minute dispatch and bid data as Parquet in object
storage, queried with DuckDB. The ingest pattern below (find the high-water
mark, fetch only what's new, upsert idempotently) is the same one that tier
will reuse.

Connection comes from DATABASE_URL. Render exposes this automatically when a
Postgres instance is attached to the service.
"""

from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

REGIONS = ["NSW1", "QLD1", "VIC1", "SA1", "TAS1"]

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS price_demand (
    region      TEXT             NOT NULL,
    settlement  TIMESTAMP        NOT NULL,
    rrp         DOUBLE PRECISION,
    demand_mw   DOUBLE PRECISION,
    PRIMARY KEY (region, settlement)
);
"""


def database_url() -> str:
    """Return a SQLAlchemy-compatible URL, normalising Render's scheme."""
    url = os.environ.get("DATABASE_URL", "").strip()
    # Render/Heroku hand out 'postgres://'; SQLAlchemy + psycopg2 needs 'postgresql://'.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


@lru_cache(maxsize=1)
def get_engine() -> Engine | None:
    url = database_url()
    if not url:
        return None
    return create_engine(url, pool_pre_ping=True, future=True)


def init_db(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(SCHEMA_DDL))


def is_configured() -> bool:
    return get_engine() is not None


def has_data(engine: Engine, region: str | None = None) -> bool:
    q = "SELECT 1 FROM price_demand"
    params: dict = {}
    if region:
        q += " WHERE region = :region"
        params["region"] = region
    q += " LIMIT 1"
    with engine.connect() as conn:
        return conn.execute(text(q), params).first() is not None


def regions_present(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT region FROM price_demand ORDER BY region"
        )).all()
    return [r[0] for r in rows]


# ----------------------------------------------------------------------------
# Aggregate queries — resampling happens in SQL so we never pull raw intervals
# into the web process.
# ----------------------------------------------------------------------------

def monthly_series(engine: Engine, region: str) -> pd.DataFrame:
    q = text("""
        SELECT date_trunc('month', settlement) AS bucket,
               avg(rrp)                                              AS mean,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY rrp)     AS median,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY rrp)     AS p95,
               stddev_samp(rrp)                                      AS std
        FROM price_demand
        WHERE region = :region
        GROUP BY 1 ORDER BY 1
    """)
    df = pd.read_sql(q, engine, params={"region": region}, parse_dates=["bucket"])
    return df.set_index("bucket")


def annual_series(engine: Engine, region: str) -> pd.DataFrame:
    q = text("""
        SELECT date_trunc('year', settlement) AS bucket,
               avg(rrp)                                          AS mean,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY rrp)  AS median
        FROM price_demand
        WHERE region = :region
        GROUP BY 1 ORDER BY 1
    """)
    df = pd.read_sql(q, engine, params={"region": region}, parse_dates=["bucket"])
    return df.set_index("bucket")


def monthly_profile(engine: Engine, region: str) -> pd.DataFrame:
    """Mean RRP per (year, calendar month), raw and capped at $300."""
    q = text("""
        SELECT extract(year  FROM settlement)::int AS yr,
               extract(month FROM settlement)::int AS mth,
               avg(rrp)                            AS mean,
               avg(least(rrp, 300))                AS mean_capped
        FROM price_demand WHERE region = :region
        GROUP BY 1, 2 ORDER BY 1, 2
    """)
    return pd.read_sql(q, engine, params={"region": region})


def diurnal_profile(engine: Engine, region: str) -> pd.DataFrame:
    """
    Mean RRP per (year, minute-of-day), raw and capped at $300 — the average
    daily shape per year. Settlement times are AEST market time (the NEM does
    not observe DST), so the diurnal curve is already in local market time.
    """
    q = text("""
        SELECT extract(year FROM settlement)::int                              AS yr,
               (extract(hour FROM settlement)*60
                + extract(minute FROM settlement))::int                        AS minute_of_day,
               avg(rrp)                                                        AS mean,
               avg(least(rrp, 300))                                            AS mean_capped
        FROM price_demand WHERE region = :region
        GROUP BY 1, 2 ORDER BY 1, 2
    """)
    return pd.read_sql(q, engine, params={"region": region})


def annual_profile(engine: Engine, region: str) -> pd.DataFrame:
    """One mean per year, raw and capped — for the 'neither toggle' view."""
    q = text("""
        SELECT extract(year FROM settlement)::int AS yr,
               avg(rrp)             AS mean,
               avg(least(rrp, 300)) AS mean_capped
        FROM price_demand WHERE region = :region
        GROUP BY 1 ORDER BY 1
    """)
    return pd.read_sql(q, engine, params={"region": region})


def month_diurnal_profile(engine: Engine, region: str) -> pd.DataFrame:
    """
    Mean RRP per (year, month, minute-of-day), raw and capped — the daily shape
    within each month. Powers the 'both toggles on' view (~6k rows over a decade).
    """
    q = text("""
        SELECT extract(year  FROM settlement)::int AS yr,
               extract(month FROM settlement)::int AS mth,
               (extract(hour FROM settlement)*60
                + extract(minute FROM settlement))::int AS minute_of_day,
               avg(rrp)             AS mean,
               avg(least(rrp, 300)) AS mean_capped
        FROM price_demand WHERE region = :region
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """)
    return pd.read_sql(q, engine, params={"region": region})


def summary(engine: Engine, region: str) -> dict:
    q = text("""
        SELECT min(settlement)                          AS start,
               max(settlement)                          AS "end",
               count(*)                                 AS intervals,
               avg(rrp)                                 AS mean_all,
               max(rrp)                                 AS max_interval,
               min(rrp)                                 AS min_interval,
               avg(CASE WHEN rrp < 0 THEN 1.0 ELSE 0.0 END) * 100 AS negative_share
        FROM price_demand WHERE region = :region
    """)
    with engine.connect() as conn:
        row = conn.execute(q, {"region": region}).mappings().first()
    if not row or row["intervals"] == 0:
        return {}

    monthly = monthly_series(engine, region)["mean"]
    peak_ts = monthly.idxmax()
    return {
        "region": region,
        "start": row["start"].strftime("%Y-%m-%d"),
        "end": row["end"].strftime("%Y-%m-%d"),
        "intervals": int(row["intervals"]),
        "mean_all": round(float(row["mean_all"]), 2),
        "max_interval": round(float(row["max_interval"]), 2),
        "min_interval": round(float(row["min_interval"]), 2),
        "negative_share": round(float(row["negative_share"]), 2),
        "latest_month": monthly.index.max().strftime("%b %Y"),
        "latest_month_mean": round(float(monthly.iloc[-1]), 2),
        "peak_month": peak_ts.strftime("%b %Y"),
        "peak_month_mean": round(float(monthly.max()), 2),
    }
