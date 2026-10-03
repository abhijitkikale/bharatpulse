# India Market Dashboard — Design

## Goal
Interactive, dark-themed, Power BI-style end-of-day dashboard for Indian markets, modelled on the 21 snapshots in this folder. Local-first (`dashboard.html`, opens offline), copy to Drive for reference. Runs daily after close via Windows scheduled task.

## Decisions (from Q&A)
- Delivery: static self-contained HTML (Plotly) built by Python; no server.
- Panels: benchmark + sector returns, global + sector rank heatmaps, breadth/trend (A/D, % above SMA50/200, golden/death cross, brutal strength/weakness, rank bands), global indices ($ toggle), FII/DII, Nifty valuation.
- Universe: official NSE indices; ~750 stocks (Nifty 500 + Microcap 250).
- Periods: 1W 1M 3M 6M 1Y 3Y 5Y (3Y/5Y annualised).
- Valuation: trailing P/E from NSE (forward P/E optional via `data/forward_pe.csv` overlay).
- Rank bands: free-float mcap if NSE report parses, else total mcap (labelled).
- Drill-down: sortable constituent table, re-sorting stock bar chart, per-stock price chart with 50/200 DMA, trend flags, CSV export, search, top gainers/losers.
- Broker APIs: last resort only (login friction).

## Architecture
`pipeline/` (Python) → `data/` (cache: prices parquet/csv, json) → `dashboard.html` (data embedded as JSON).
- `sources.py`: per-source fetchers, each fails independently, returns data + status.
- `compute.py`: returns, ranks, SMA flags, breadth, bands.
- `build.py`: renders template with embedded JSON.
- `run_daily.bat` + scheduled task (weekdays ~19:00 IST).
- Fake-data mode for UI testing.

## Error handling
Per-panel "as of" date and stale/failed badge; cached data reused when a source fails.

## Testing
Unit tests for return/rank/flag maths on fixed data; browser smoke test (charts render, drill-down works, no console errors).
