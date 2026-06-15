# asx-elec-futures

Analysis and visualisation of Australian electricity markets, starting with NSW
spot ("pool") prices and building toward ASX electricity futures.

## What's here now

A small Flask dashboard showing the **last 10 years of the NSW Regional
Reference Price (RRP)** — the half-hourly NEM spot price used for settlement —
sourced from AEMO's public *Aggregated price and demand* dataset.

- `data_fetch.py` — downloads monthly AEMO price/demand CSVs for a region.
- `build_dataset.py` — fetches a window, resamples to daily/monthly/annual, caches parquet.
- `app.py` — Flask app that serves an interactive Plotly chart of the history.
- `templates/index.html` — the dashboard page.

## Run locally

```bash
pip install -r requirements.txt
python build_dataset.py --region NSW1 --years 10   # fetches ~120 files, caches to data/
python app.py                                      # http://localhost:8000
```

## Deploy on Render

The included `render.yaml` is a blueprint. Render runs `build_dataset.py` at
build time (it can reach AEMO directly), then serves the app with gunicorn.
Point a new Blueprint/Web Service at this repo and deploy — auto-deploys on push.

## Data source

`https://aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{YYYYMM}_{REGION}1.csv`

Columns: `REGION, SETTLEMENTDATE, TOTALDEMAND, RRP, PERIODTYPE` (30-min intervals).
If AEMO ever moves this endpoint, override it with the `AEMO_BASE_URL` env var —
no code change needed.

## Notes

- RRP can be negative (oversupply) and can spike to the market price cap during
  scarcity — monthly averaging keeps the long-run trend readable while the
  daily series preserves the spikes.
- Regions: `NSW1`, `QLD1`, `VIC1`, `SA1`, `TAS1`.
