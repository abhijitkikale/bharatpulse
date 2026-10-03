# BharatPulse — India Markets Dashboard

Live: https://abhijitkikale.github.io/bharatpulse/ (rebuilt automatically every weekday at 19:00 IST by GitHub Actions)

Open `dashboard.html` (works offline; double-click). Click any bar/row to drill into sectors, indices, stocks.

- Refresh now: double-click `run_daily.bat` (first run is slow, later runs are incremental).
- Auto-refresh weekdays 19:00: run `install_schedule.bat` once.
- Rebuild from cached data only: `cd pipeline && python run.py --cached`.
- Tests: `python -m pytest tests`.
- Valuation: trailing P/E, P/B, yield from NSE; forward P/E computed from Yahoo analyst consensus (next fiscal year), recorded daily in `data/fwd_pe.csv`.
- Sharing: Google Drive shows HTML as text; download `dashboard.html` and open it locally.

Data: NSE archives (indices, constituents, FII/DII), Yahoo Finance (stocks, global indices, FX).
Private Bank and Capital Market constituents are approximations (NSE publishes no file).

Educational/informational only - not investment advice. Market data is from NSE and Yahoo Finance and is subject to their terms.
