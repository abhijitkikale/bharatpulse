# BharatPulse — India Markets Dashboard

Live: https://abhijitkikale.github.io/bharatpulse/ (rebuilt every weekday evening, about 19:00 IST, by GitHub Actions; `refresh.yml` can also be started by hand from the Actions tab)

Open `dashboard.html` (works offline; double-click). Click any bar/row to drill into sectors, indices, stocks.

## Tabs
Sectors · Breadth & Trend · Market Activity · Flows & Valuation · Risk Monitor · Options · Volume Profile · 3D Surface · Momentum Stocks

- **Options**: open interest by strike, max pain, put-call ratio, GEX (Black-76, dealers long calls / short puts: gamma flip, GEX walls, GEX profile), participant OI, India VIX, volume-profile levels, and a **"Levels for TradingView"** text box (Copy levels) that feeds the TradingView indicator.
- **Volume Profile**: POC / VAH / VAL for 1 day and the last 5 sessions from 1-minute index candles with index volume.
- **Momentum Stocks**: dual momentum score = sector score + score within sector (0 best to 100 worst), weighted (default 0.6 sector / 0.4 stock, adjustable). Periods: by default **non-overlapping slices** of time (1M = the month before the latest week, 3M = the two months before the latest month ...), so one big day counts only in the newest slice; the latest week is skipped by default. Switchable to plain cumulative returns, with a weight per period (default 1). The same method drives the composite rank on the Sectors tab. 1D returns are never used in the scores. Ties go to the higher return over the horizon.

## Morning when you leave home before the market (hands-free day)
Times are India time (IST). Your PC clock is IST minus 2:30.
1. **Once:** run `install_morning_task.bat`. It makes two weekday tasks: 06:35 IST opens `Refresh BharatPulse.bat` so the Fyers sign-in page is waiting (04:05 on the PC clock), and 09:20 IST checks that everything runs and warns you on Telegram if not (06:50 on the PC clock). The PC must be on and logged in to Windows.
2. **Every morning, between 06:31 IST and when you leave:** sign in to Fyers (PIN / code). **Do not sign in before 06:30 IST**: Fyers tokens end at 06:30 IST, so an earlier sign-in expires within minutes (the helper page now says "signed in too early"). This is the only manual step.
3. Then it runs by itself until 15:40: the PC stays awake, live updates and Telegram levels run, OBS opens and the YouTube broadcast starts (see below), TradingView sends the opening candles at 09:15 to 09:30, and at 09:31 Telegram sends **"BharatPulse stream: READY"** (or CHECK). The same token covers the evening cloud build at 19:00.
4. If something fails while you are away you get a Telegram warning (09:20 check, 09:31 check, or "broadcast stopped"). The Fyers sign-in page only works on the PC itself.

## OBS and YouTube (automatic)
`tools/obs_control.py` runs inside the stream program (only when `data/obs.json` exists): it opens OBS minimised, makes sure the scene "BharatPulse" with the stream page (`http://127.0.0.1:8766/?clean=1`, 1920x1080) exists, starts the broadcast and stops it at 15:35 IST (or earlier if the feed has been bad for 10 minutes; it then stays off until the next day, and a broadcast you stop yourself is not restarted). Modes: **direct** (starts right after your sign-in, the chart shows a countdown to the open in the centre until 09:15 IST), **at_open** (starts at 09:31 IST when the feed is healthy), **off** (only opens OBS).
One-time setup:
1. OBS: Tools > WebSocket Server Settings > enable it, set a strong password. OBS: Settings > Stream > YouTube, sign in.
2. `python tools/obs_setup.py` (or `python tools/obs_setup.py at_open`): paste the OBS password; it saves it only in `data/obs.json`, connects and creates the scene.
3. YouTube Studio: enable live streaming on the channel (can take up to 24 hours), set the default privacy to **Private** and share it with your friends: open the live stream / video in Studio, Visibility > Private > **Share privately** and add each friend's Google account email (up to 50). They watch while signed in with that account.
The page's countdown also shows after the close, counting to the next weekday open (exchange holidays are not known).

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
`tools/stream_server.py` (started automatically with the live updates by `Refresh BharatPulse.bat`, or `python tools/stream_server.py`) serves a full-screen chart at **http://127.0.0.1:8766/** on this PC: Nifty 1-minute candles from Fyers (every 10 s), all end-of-day levels (read from the published dashboard page), live GEX levels (every 5 minutes), "Now" volume levels, daily and weekly VWAP, the EOD / NOW regime box, and a running **"not SEBI registered, not financial advice"** banner at the top and bottom. Nothing is pasted by hand. In OBS add it as a **Browser source** (1920x1080) and stream to YouTube. On **http://127.0.0.1:8766/** it also shows the setups table (CALL / PUT, strike, level; same rules as the live Pine indicator, replayed on today's candles) and CALL / PUT markers on the candles, for your own screen. It also runs a **paper trade tracker** (percentages only, no orders): the first setup becomes a trade (CALL = at-the-money call, PUT = at-the-money put, nearest weekly expiry, entry at the option's 1-minute close from Fyers), held until **+40%** or **-10%** of the entry premium (a candle touching both counts as the stop; a trade still open at 15:29 closes at day end). Setups that confirm while a trade is open are only listed ("ignored"); the next setup after the exit becomes the next trade; no new trade after 14:45 (setups still show). The strip under the header shows the open / latest trade (entry, target, stop, live premium, % profit or loss) and the day total. Now VAH / Now VAL give setups only from 09:45 (their profile is too thin before). No brokerage, taxes or slippage. The opening candles (09:15 to 09:30) can come from TradingView through a webhook alert and the free ntfy.sh relay (`python tools/stream_relay_setup.py`; steps in `tradingview/LIVE-SETUP.md`), so the VWAP matches the TradingView chart; Fyers supplies the rest. Use **http://127.0.0.1:8766/?clean=1** (no setups, trades or P&L, no markers) as the OBS Browser source; `&theme=dark` or `&theme=light` picks the background (default light, or the Light / Dark button). The PC must stay on and signed in to Fyers (sign in each morning); if the feed stops the page says DATA STALE. Check SEBI rules (investment advice / research analyst registration) and NSE / Fyers / TradingView data-redistribution terms before streaming publicly.

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
