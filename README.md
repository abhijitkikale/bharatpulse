# BharatPulse — India Markets Dashboard

Live: https://abhijitkikale.github.io/bharatpulse/ (rebuilt every weekday evening, about 19:00 IST, by GitHub Actions; `refresh.yml` can also be started by hand from the Actions tab)

Open `dashboard.html` (works offline; double-click). Click any bar/row to drill into sectors, indices, stocks.

## Tabs
Sectors · Breadth & Trend · Market Activity · Flows & Valuation · Risk Monitor · Options · Volume Profile · 3D Surface · Momentum Stocks

- **Options**: open interest by strike, max pain, put-call ratio, GEX (Black-76, dealers long calls / short puts: gamma flip, GEX walls, GEX profile), participant OI, India VIX, volume-profile levels, and a **"Levels for TradingView"** text box (Copy levels) that feeds the TradingView indicator.
- **Volume Profile**: POC / VAH / VAL for 1 day and the last 5 sessions from 1-minute index candles with index volume.
- **Momentum Stocks**: dual momentum score = sector score + score within sector (0 best to 100 worst), weighted (default 0.6 sector / 0.4 stock, adjustable). Periods: by default **non-overlapping slices** of time (1M = the month before the latest week, 3M = the two months before the latest month ...), so one big day counts only in the newest slice; the latest week is skipped by default. Switchable to plain cumulative returns, with a weight per period (default 1). The same method drives the composite rank on the Sectors tab. 1D returns are never used in the scores. Ties go to the higher return over the horizon.

## Daily routine (one interface: `Refresh BharatPulse.bat`)
Double-click it, press **Sign in and start**, type your Fyers PIN/TOTP on Fyers' own page (this program never sees them). The helper swaps the one-time code for a one-day, data-only token and stores it as encrypted GitHub secrets.
- **Before 15:40 IST on a weekday (morning)**: it starts the **live updates** (below). Keep the window open until about 15:45. The same token also serves the evening cloud build. If a valid token is already saved, live updates start by themselves.
- **After that (evening)**: it starts the cloud build and shows its progress.
- Fyers tokens expire about 06:30 IST, so sign in each morning before 09:15. The Fyers app secret lives only in your Windows environment variables (`FYERS_APP_ID`, `FYERS_APP_SECRET`); redirect URL registered on Fyers: `http://127.0.0.1:5050/`. CLI fallback: `python tools/fyers_login.py`.
- Without a valid token the build falls back to NSE/Yahoo data and says so in its status list. Do not start a manual cloud build while you sign in (two builds at once collide when saving the history files).

## Live updates (every 5 minutes, 09:20–15:40 IST, weekdays)
`tools/live_loop.py` (run inside the helper) pulls Fyers 1-minute index candles and the **current-expiry** option chain of **Nifty** (Bank Nifty can be added back in `pipeline/live_gex.py`, `LIVE_SYMBOLS`).
- Volume profile (POC / VAH / VAL, weekly, VWAP) and live GEX levels (gamma flip, GEX call / put wall, OI walls, max pain) go into `data/live.json`, force-pushed as one amended commit to the `live-data` branch. The dashboard polls it every 2 minutes and shows a green Live banner on the Options and Volume Profile tabs; the page hides it once the end-of-day build for that date exists.
- **Telegram** (bot `GEXINDICATORBOT`): when a live level moves by at least one strike (50 points Nifty) the helper sends the text block for the indicator's Live box. One-time setup: press Start on https://t.me/GEXINDICATORBOT, then `python tools/telegram_setup.py` and paste the token. Token and chat id stay in `data/telegram.json` on this PC (git-ignored).

## Live stream page (for OBS / YouTube Live; first version, Nifty only)
`tools/stream_server.py` (started automatically with the live updates by `Refresh BharatPulse.bat`, or `python tools/stream_server.py`) serves a full-screen chart at **http://127.0.0.1:8766/** on this PC: Nifty 1-minute candles from Fyers (every 10 s), all end-of-day levels (read from the published dashboard page), live GEX levels (every 5 minutes), "Now" volume levels, daily and weekly VWAP, the EOD / NOW regime box, and a running **"not SEBI registered, not financial advice"** banner at the top and bottom. Nothing is pasted by hand. In OBS add it as a **Browser source** (1920x1080) and stream to YouTube. It shows no CALL / PUT markers or strikes. The PC must stay on and signed in to Fyers (sign in each morning); if the feed stops the page says DATA STALE. Check SEBI rules (investment advice / research analyst registration) and NSE / Fyers / TradingView data-redistribution terms before streaming publicly.

## TradingView indicators (`tradingview/`)
Pine v6, nothing is fetched: the indicators draw text you paste into Settings > Inputs. Details in `tradingview/LIVE-SETUP.md`.
- `BharatPulse-Levels.pine` (Rev 0), `BharatPulse-Levels-rev1.pine` (Rev 1: the NEXT candle after the retest decides CALL / PUT).
- **`BharatPulse-Levels-live.pine`** (current): two paste boxes (end-of-day text from the dashboard, live text from Telegram), self-computed "Now" volume levels (POC / VAH / VAL, weekly, daily and weekly VWAP), a two-line regime box (EOD / NOW), setups table at the top right, and setups only on the levels you tick (default: GEX call / put wall, POC, VAH, VAL, weekly POC / VAH / VAL, daily and weekly VWAP, Now VAH / VAL on live candles). Live Telegram levels and other Now lines are reference only. Setup rules: touch > doji > retest (original rules), plus break-and-retest and a wider doji zone, plus a VWAP break signal (N candles on one side, a candle closes through, the next candle confirms).
- `PUBLISH-DESCRIPTION.md` is the text for the published Rev 0 script. The Pine files cannot be compiled outside TradingView: paste, and report any red error.

## Other commands
- Rebuild from cached data only: `cd pipeline && python run.py --cached`.
- Tests: `python -m pytest tests`.
- Older local route: `run_daily.bat` / `install_schedule.bat` (scheduled local refresh); the cloud build above replaces it.
- Valuation: trailing P/E, P/B, yield from NSE; forward P/E from Yahoo analyst consensus, recorded daily in `data/fwd_pe.csv`.
- Sharing: Google Drive shows HTML as text; download `dashboard.html` and open it locally.

## Data
NSE archives (indices, constituents, F&O bhavcopy, participant OI, FII/DII), Yahoo Finance (stocks, global indices, FX), Fyers data-only API (1-minute index candles with index volume, option chain).
Private Bank and Capital Market constituents are approximations (NSE publishes no file).

Educational/informational only - not investment advice. Market data is from NSE, Yahoo Finance and Fyers and is subject to their terms.
