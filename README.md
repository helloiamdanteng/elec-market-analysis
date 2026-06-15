# elec-market-analysis

Analysis and visualisation of Australian electricity markets — starting with NEM
spot ("pool") prices and building toward 5-minute dispatch, all regions, and
eventually bidstack analysis and ASX electricity futures.

## Architecture

Data is **stored, not re-scraped**. A scheduled job ingests AEMO data into
Postgres; the web app only queries it.

```
AEMO  ──(ingest.py, scheduled)──>  Postgres  ──(SQL aggregates)──>  Flask + Plotly
```

- `data_fetch.py` — downloads AEMO monthly price/demand CSVs for a region.
- `db.py` — Postgres schema + aggregate queries (resampling done in SQL).
- `ingest.py` — incremental ingest: fetches only data newer than what's stored,
  upserts idempotently (`ON CONFLICT DO NOTHING`).
- `app.py` + `templates/index.html` — dashboard with a region selector.
- `render.yaml` — Blueprint: Postgres + web service + daily ingest cron.

### Tier 2 (planned)

5-minute dispatch and **bid** data are far larger (bids are ~30M+ rows/year).
Those land as Parquet in object storage (Cloudflare R2) and are queried with
DuckDB. The ingest pattern here — high-water mark, fetch-only-new, idempotent
upsert — is the same one that tier reuses.

## Data model

Table `price_demand (region, settlement, rrp, demand_mw)`, PK `(region, settlement)`.
`rrp` is the Regional Reference Price ($/MWh), the NEM spot price used for
settlement. Regions: `NSW1`, `QLD1`, `VIC1`, `SA1`, `TAS1`.

## Run locally

Needs a Postgres instance (point `DATABASE_URL` at one — a local server or a
Render database):

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql://user:pass@host:5432/elec
python ingest.py --backfill        # one-time: last 10 years, all regions
python app.py                      # http://localhost:8000
```

After backfill, `python ingest.py` (no flag) appends only new intervals.

## Deploy on Render

1. **New → Blueprint**, point at this repo. It creates the Postgres database,
   the web service, and the daily ingest cron, wiring `DATABASE_URL` automatically.
2. **Backfill once:** open a Shell on the `elec-market-analysis` service and run
   `python ingest.py --backfill`. (Or trigger the `elec-ingest` job after
   temporarily setting its command to include `--backfill`.)
3. Open the service URL. The daily cron keeps it current from then on.

> The DB plan in `render.yaml` (`basic-256mb`) is a starting point — pick your
> tier in the dashboard. Render occasionally renames plans; if the Blueprint
> flags it, set the plan there.

## Data source

`https://aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{YYYYMM}_{REGION}1.csv`
Columns: `REGION, SETTLEMENTDATE, TOTALDEMAND, RRP, PERIODTYPE` (30-min intervals).
Override the base URL with `AEMO_BASE_URL` if AEMO moves the endpoint.
