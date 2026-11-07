# BharatPulse — India Markets Dashboard

Live: https://abhijitkikale.github.io/bharatpulse/ (rebuilt every weekday evening, about 19:00 IST, by GitHub Actions; `refresh.yml` can also be started by hand from the Actions tab)

Open `dashboard.html` (works offline; double-click). Click any bar/row to drill into sectors, indices, stocks.

Times below are India time (IST). The PC this runs on has its clock at IST minus 2:30.

## Contents
1. [Tabs](#tabs)
2. [A normal weekday morning](#a-normal-weekday-morning)
3. [The helper: `Refresh BharatPulse.bat`](#the-helper-refresh-bharatpulsebat)
4. [Live updates and Telegram](#live-updates-and-telegram)
5. [The live stream page](#the-live-stream-page)
6. [OBS and YouTube](#obs-and-youtube)
7. [TradingView indicators](#tradingview-indicators)
8. [What is saved on this PC](#what-is-saved-on-this-pc)
9. [Other commands, data, notes](#other-commands-data-notes)

## Tabs
Sectors · Breadth & Trend · Market Activity · Flows & Valuation · Risk Monitor · Options · Volume Profile · 3D Surface · Momentum Stocks

- **Options**: open interest by strike, max pain, put-call ratio, GEX (Black-76, dealers long calls / short puts: gamma flip, GEX walls, GEX profile), participant OI, India VIX, volume-profile levels, and a **"Levels for TradingView"** text box (Copy levels) that feeds the TradingView indicator.
- **Volume Profile**: POC / VAH / VAL for 1 day and the last 5 sessions from 1-minute index candles with index volume.
- **Momentum Stocks**: dual momentum score = sector score + score within sector (0 best to 100 worst), weighted (default 0.6 sector / 0.4 stock, adjustable). By default each period is its own **non-overlapping slice** of time (1M = the month before the latest week, 3M = the two months before the latest month ...), so one big day counts only in the newest slice; the latest week is skipped by default. Switchable to plain cumulative returns, with a weight per period (default 1). The same method drives the composite rank on the Sectors tab. 1D returns are never used in the scores. Ties go to the higher return over the horizon.

## A normal weekday morning
For when you leave home at 07:00 to 07:30, before the market opens. The only manual step is the Fyers sign-in.
1. **Once:** run `install_morning_task.bat`. It makes two weekday Windows tasks: 06:35 IST (04:05 on the PC clock) opens `Refresh BharatPulse.bat` so the Fyers sign-in page is already waiting, and 09:20 IST (06:50 on the PC clock) checks everything and warns you on Telegram if not. The PC must be on and logged in to Windows.
2. **Every morning, after 06:30 IST and before you leave:** sign in to Fyers (PIN / code). Fyers tokens end at 06:30 IST, so an earlier sign-in expires within minutes (the helper page says "SIGNED IN TOO EARLY").
3. **Then it runs by itself until about 15:40:** the PC stays awake, live updates and Telegram levels run, the stream page and OBS start, the YouTube broadcast starts (direct mode), TradingView sends the opening candles at 09:15 to 09:30, and at 09:31 Telegram sends **"BharatPulse stream: READY"** (or "CHECK THE FYERS SIGN-IN", or a warning that the TradingView opening candles did not arrive).
4. **Share the day's link:** the broadcast is Unlisted and every broadcast has a **new link**. When Telegram says "YouTube broadcast STARTED", open YouTube Studio > Stream > **Share**, copy the link and send it to your friends.
5. **Evening:** nothing to do. The same token covers the 19:00 cloud build.
6. If something fails while you are away you get a Telegram warning (09:20 check, 09:31 check, "broadcast did NOT start", "broadcast stopped"). The Fyers sign-in page works only on the PC itself.

Weekends and NSE holidays are skipped by the checks and the broadcast (see the NSE calendar below).

## The helper: `Refresh BharatPulse.bat`
Runs `tools/refresh_app.py` and opens a page at http://127.0.0.1:5050/ (this PC only).
- **Sign in and start:** you type your Fyers PIN/TOTP on Fyers' own page (this program never sees it). The helper swaps the one-time code for a one-day, data-only token and stores it as encrypted GitHub secrets. Before 15:40 IST on a weekday it then starts the live updates and the stream; after that it starts the cloud build and shows its progress. If a valid token is already saved it starts the live updates by itself.
- **Token rules:** the expiry is worked out in IST (06:30 IST each day), whatever the PC clock says. The Fyers app secret lives only in your Windows environment variables (`FYERS_APP_ID`, `FYERS_APP_SECRET`); the redirect URL registered on Fyers is `http://127.0.0.1:5050/`. CLI fallback: `python tools/fyers_login.py`. Without a valid token the build falls back to NSE/Yahoo data and says so.
- **Live dashboard / OBS card:** status rows for the stream page, OBS and the YouTube broadcast, with buttons **Open the live dashboard**, **Start OBS** (opens OBS, or brings its window to the front if it is hidden in the tray), **Go live on YouTube** and **Stop broadcast**. They work in every mode, and only this page can press them.
- The helper keeps the PC awake while it runs. Do not start a manual cloud build while you sign in (two builds at once collide when saving the history files).

## Live updates and Telegram
`tools/live_loop.py` (run inside the helper) every 5 minutes, 09:20–15:40 IST: pulls Fyers 1-minute index candles and the **current-expiry** option chain of **Nifty** (Bank Nifty can be added back in `pipeline/live_gex.py`, `LIVE_SYMBOLS`).
- The volume profile (POC / VAH / VAL, weekly, VWAP) and live GEX levels (gamma flip, GEX call / put wall, OI walls, max pain) go into `data/live.json`, force-pushed as one amended commit to the `live-data` branch. The dashboard polls it every 2 minutes and shows a green Live banner on the Options and Volume Profile tabs; the page hides it once the end-of-day build for that date exists.
- **Telegram** (bot `GEXINDICATORBOT`): when a live level moves by at least one strike (50 points Nifty) the helper sends the text block for the indicator's Live box. One-time setup: press Start on https://t.me/GEXINDICATORBOT, then `python tools/telegram_setup.py` and paste the token. Token and chat id stay in `data/telegram.json` (git-ignored).
- Other Telegram messages: the 09:20 check, the 09:31 READY check, "broadcast STARTED / did NOT start / stopped".

## The live stream page
`tools/stream_server.py` (started with the live updates, or `python tools/stream_server.py`; `--port 8767` for a test copy) serves a full-screen chart at **http://127.0.0.1:8766/** on this PC. Nothing is pasted by hand.
- **Layout:** header (Nifty 1-minute, price, data status, Light / Dark button), a **trade strip** (see below), a **setups line**, and a left panel with the **EOD / NOW gamma regime** and **every level in one table** (sorted by price, with distance from the price). The chart fills the rest, its price axis fitted to the day's high and low; level lines are drawn in the same colours as the table. Disclaimer banners run along the top and bottom: "not SEBI registered, not financial advice, education only". Press F11 for full screen.
- **Data:** Nifty candles from Fyers every 10 s in session; end-of-day levels read from the published dashboard page; live GEX every 5 minutes; "Now" volume levels (0.25-point rows, close to TradingView's own profile), daily and weekly VWAP.
- **Countdown timer:** in the centre of the chart whenever no session is running ("NSE opens in 02:34:10", "Market closed. Next session in 2D 07:31:53"). It follows the **NSE holiday calendar**: `pipeline/nse_calendar.py` reads NSE's official holiday list (cached in `data/nse_calendar.json`, refreshed about twice a day), skips holidays ("NSE holiday (Dussehra). Next session in ...") and counts special sessions such as Muhurat trading as sessions. NSE publishes the Muhurat timing late; until then the timer says "timing not announced yet" (Diwali Laxmi Pujan, Sun 8 Nov 2026) and it picks the times up by itself once NSE lists them. If you know them earlier, put them in `data/nse_special_sessions.json`, e.g. `{"2026-11-08": {"open": "18:15", "close": "19:15", "name": "Muhurat trading"}}`. The stream's fast polling, the broadcast start / stop and the 09:20 check use the same calendar.
- **Setups (CALL / PUT)**, the same rules as the Pine indicator, replayed on the day's candles. Original rule: a candle touches a level, a doji forms at it, price leaves and retests, the next candle decides CALL (close above) or PUT (close below), tagged bounce / cross. **A**: a candle that closes through the level also counts as step 2. **B**: a doji counts within 12 points of the level. **C, VWAP break**: the previous 8 candles on one side of the VWAP, a candle closes through it, the next candle closes beyond it. **D, VWAP bounce**: the previous 5 candles on one side, a candle touches the VWAP and closes back on that side, the next candle closes beyond it; at most one per 10 candles per VWAP. A candle reports a level only once. Levels that give setups: end-of-day GEX call / put wall, POC, VAH, VAL, weekly POC / VAH / VAL; daily and weekly VWAP; Now VAH / Now VAL (only from 09:45, when their profile is no longer thin).
- **Paper trade tracker** (percentages only, no orders): the first setup becomes a trade (CALL = at-the-money call, PUT = at-the-money put, nearest weekly expiry, entry at the option's 1-minute close from Fyers), held until **+40%** or **-10%** of the entry premium (a candle touching both counts as the stop; a trade still open at 15:29 closes at day end). Setups that confirm while a trade is open are only listed ("ignored: trade N is open"); the next setup after the exit becomes the next trade; no new trade after 14:45 (setups still show). The trade strip shows every trade of the day (earlier ones as chips) and the open or latest one in detail, with the day total. No brokerage, taxes or slippage.
- **After the close** the page keeps showing the day's trades: the levels that were valid during the session are saved in `data/stream_eod/` (one file per close date) and replayed, and the option prices come from the session's own day (or from the journal if Fyers no longer has them).
- **Opening candles from TradingView:** Fyers' 09:15 candle differs from TradingView's (open, high and above all volume) and that candle dominates the VWAP. Your TradingView sends its own 09:15 to 09:30 candles through a webhook alert and the free ntfy.sh relay (`python tools/stream_relay_setup.py`, steps in `tradingview/LIVE-SETUP.md`); the page uses them for the opening and Fyers afterwards, and keeps them per day in `data/stream_open/`. The header shows "opening candles: TradingView (15)".
- **Addresses:** `http://127.0.0.1:8766/` is the full view for your own screen. `http://127.0.0.1:8766/?clean=1` hides the setups, trades, profit and loss and markers (use this one for OBS / YouTube). `&theme=dark` or `&theme=light` picks the background (default light).
- If the feed stops the page says DATA STALE. YouTube viewers are always 10 to 30 seconds behind (set Low latency in Studio to shorten it).

## OBS and YouTube
`tools/obs_control.py` runs inside the stream program (only when `data/obs.json` exists). It opens OBS minimised, makes sure the scene **BharatPulse** with a Browser source `http://127.0.0.1:8766/?clean=1&theme=dark` (1920x1080) exists, starts the broadcast and stops it at 15:35 IST, or earlier if the feed has been bad for 10 minutes (then it stays off until the next day, and a broadcast you stop yourself is not restarted). Before starting it selects the BharatPulse scene and reloads the page; it never changes your other scenes. After StartStream it checks that OBS is really sending and tells you on Telegram if not (one attempt a day, so OBS's error window does not pop up repeatedly).
- **Modes** (`data/obs.json`, set with `python tools/obs_setup.py [direct|at_open|off]`): **direct** (starts right after your sign-in; the chart shows the countdown until the open), **at_open** (starts at 09:31 IST once the feed is healthy), **off** (only opens OBS; use the helper page's buttons). Currently **direct**.
- **One-time setup:**
  1. OBS: Tools > WebSocket Server Settings > enable it (OBS made its own password; it listens on the network, so keep the password strong). The setup script saved it in `data/obs.json` (git-ignored).
  2. OBS: Settings > Stream > YouTube - RTMPS > **Use stream key** and paste the key from YouTube Studio (Stream page, "Default stream key"). Do not rely on "Connect Account": an automatic start then fails with "rtmp_output failed to start" because OBS has no key. Keep the key private.
  3. YouTube Studio: enable live streaming on the channel (can take up to 24 hours after you request it).
- **Sharing: Unlisted, you paste the link each day.** Every OBS go-live creates a new broadcast with a new link, and Private invitations do not carry over. Set Studio > Stream > Edit > Visibility **Unlisted** (anyone with the link can watch; not listed on the channel or in search; ask friends not to forward it). Turn live chat off, set **Low latency**, and put "not SEBI registered, not financial advice, education only" in the title or description. (With Private instead, every broadcast needs each Google account invited again, and the invited person must be signed in with exactly that account.)
- A test broadcast was run successfully on 2026-11-07 (the stream reached YouTube).
- Check SEBI rules (investment advice / research analyst registration) and the NSE, Fyers and TradingView data-redistribution terms before sharing widely: a disclaimer banner does not replace registration or a data licence.

## TradingView indicators
Pine v6, nothing is fetched: the indicators draw text you paste into Settings > Inputs. Details in `tradingview/LIVE-SETUP.md`.
- `tradingview/BharatPulse-Levels.pine` (Rev 0), `BharatPulse-Levels-rev1.pine` (Rev 1: the next candle after the retest decides CALL / PUT).
- **`BharatPulse-Levels-live.pine`** (current): two paste boxes (end-of-day text from the dashboard, live text from Telegram), self-computed "Now" volume levels, daily and weekly VWAP, a two-line EOD / NOW regime box, setups table top right, setups only on the levels you tick, the setup rules A, B, C, D above, and the "Stream feed" alert that sends the opening candles to the stream program.
- `PUBLISH-DESCRIPTION.md` is the text for the published Rev 0 script. The Pine files cannot be compiled outside TradingView: paste, and report any red error.

## What is saved on this PC
All under `data/`, which is **not** part of the public repository.
| Path | What |
|---|---|
| `journal/YYYY-MM-DD.json`, `journal/trades.csv` | **Trading journal backup**: the levels that were valid, every setup and what happened to it, every paper trade (contract, entry / target / stop / exit, result), the option's 1-minute candles from entry to exit, the day's index candles, regime, last live GEX. `trades.csv` has one row per trade (opens in Excel). Copy this folder to OneDrive or a drive for a second copy. |
| `stream_eod/` | one end-of-day levels file per close date (replayed after the evening build) |
| `stream_open/` | the TradingView opening candles, one file per day |
| `nse_calendar.json`, `nse_special_sessions.json` | NSE holidays (cached) and your own special-session times |
| `obs.json`, `telegram.json`, `stream_relay.json`, `fyers_token.json` | settings and secrets for OBS, Telegram, the TradingView relay and Fyers |
| `stream_state.json`, `live.json` | the latest stream page state and live snapshot |

## Other commands, data, notes
- Tests: `python -m pytest tests` (the OBS tests skip when the websocket libraries are not installed, which is the case on GitHub's build machine; the cloud build runs the tests first, so a test that needs a missing library blocks publishing).
- Rebuild from cached data only: `cd pipeline && python run.py --cached`.
- Older local route: `run_daily.bat` / `install_schedule.bat` (scheduled local refresh); the cloud build replaces it.
- Valuation: trailing P/E, P/B, yield from NSE; forward P/E from Yahoo analyst consensus, recorded daily in `data/fwd_pe.csv`.
- Sharing the dashboard file: Google Drive shows HTML as text; download `dashboard.html` and open it locally.
- Data: NSE archives (indices, constituents, F&O bhavcopy, participant OI, FII/DII, holiday list), Yahoo Finance (stocks, global indices, FX), Fyers data-only API (1-minute index candles with index volume, option chain and option candles). Private Bank and Capital Market constituents are approximations (NSE publishes no file).

Educational/informational only - not investment advice. Market data is from NSE, Yahoo Finance, Fyers and TradingView and is subject to their terms.
