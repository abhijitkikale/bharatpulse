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

## Later additions (after the first version; see README.md for the current state)
- Hosting and refresh: GitHub Pages + GitHub Actions (`refresh.yml`), one local launcher `Refresh BharatPulse.bat` for the Fyers sign-in.
- Fyers data-only API: 1-minute index candles with index volume, option chain (provisional F&O when NSE's file is late), live updates every 5 minutes (`tools/live_loop.py`, `live-data` branch).
- Tabs added: Risk Monitor, Options (OI, max pain, GEX, participants, volume profile, TradingView levels text), Volume Profile, 3D Surface, Momentum Stocks.
- Momentum / sector composite: non-overlapping periods, skip latest week, selectable method and per-period weights.
- TradingView indicators (`tradingview/`) and a Telegram bot for live levels.
- Live stream page (`tools/stream_server.py`, `pipeline/stream_*.py`): full-screen Nifty chart with every level in a table, setups and a paper trade tracker (one trade at a time, +40% / -10% on the option premium), a countdown that follows the NSE holiday calendar (`pipeline/nse_calendar.py`), the TradingView opening candles through a webhook alert and the ntfy.sh relay, and a trading journal backup (`data/journal/`).
- Hands-free day: Windows morning tasks (`install_morning_task.bat`), Fyers token expiry handled in IST, OBS and YouTube automation (`tools/obs_control.py`, `tools/obs_setup.py`), a helper-page card with the live dashboard / OBS / broadcast buttons, Telegram checks at 09:20 and 09:31, Unlisted sharing with a new link each day.
- Cloud build note: the build machine runs the tests first and has no websocket libraries, so tests that need them skip there.

## Error handling
Per-panel "as of" date and stale/failed badge; cached data reused when a source fails.

## Testing
Unit tests for return/rank/flag maths on fixed data; browser smoke test (charts render, drill-down works, no console errors).
