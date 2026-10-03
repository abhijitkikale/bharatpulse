# BharatPulse — India Markets Dashboard

Live: https://abhijitkikale.github.io/bharatpulse/ (rebuilt automatically every weekday at 19:00 IST by GitHub Actions)

Open `dashboard.html` (works offline; double-click). Click any bar/row to drill into sectors, indices, stocks.

- Refresh now: double-click `run_daily.bat` (first run is slow, later runs are incremental).
- Auto-refresh weekdays 19:00: run `install_schedule.bat` once.
- Rebuild from cached data only: `cd pipeline && python run.py --cached`.
- Tests: `python -m pytest tests`.
- Optional forward P/E: not wired yet; trailing P/E/P/B/yield come from NSE.
- Sharing: Google Drive shows HTML as text; download `dashboard.html` and open it locally.

Data: NSE archives (indices, constituents, FII/DII), Yahoo Finance (stocks, global indices, FX).
Private Bank and Capital Market constituents are approximations (NSE publishes no file).

Educational/informational only - not investment advice. Market data is from NSE and Yahoo Finance and is subject to their terms.
