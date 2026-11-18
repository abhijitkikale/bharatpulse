# BharatPulse — India Markets Dashboard

Live: https://abhijitkikale.github.io/bharatpulse/ (rebuilt every weekday evening, about 19:00 IST, by GitHub Actions; `refresh.yml` can also be started by hand from the Actions tab)

Open `dashboard.html` (works offline; double-click). Click any bar/row to drill into sectors, indices, stocks.

Times below are India time (IST). The PC this runs on has its clock at IST minus 2:30.

## Contents
1. [Strategy rules (current)](#strategy-rules-current-18-nov-2026)
2. [Tabs](#tabs)
3. [A normal weekday morning](#a-normal-weekday-morning)
4. [The helper: `Refresh BharatPulse.bat`](#the-helper-refresh-bharatpulsebat)
5. [Live updates and Telegram](#live-updates-and-telegram)
6. [The live stream page](#the-live-stream-page)
7. [OBS and YouTube](#obs-and-youtube)
8. [Daily snap video](#daily-snap-video-56-seconds-vertical-for-mobile)
9. [TradingView indicators](#tradingview-indicators)
10. [What is saved on this PC](#what-is-saved-on-this-pc)
11. [Other commands, data, notes](#other-commands-data-notes)
12. [Live GEX text pasted into TradingView](#live-gex-text-pasted-into-tradingview-automatically) · [Trading journal page](#trading-journal-page) · [Telegram for friends](#telegram-for-friends-and-control-from-your-phone) · [Order router](#order-router-automatic-orders-off-by-default)
13. [Entry confirmation and the TradingView alert log](#entry-confirmation-and-the-tradingview-alert-log-added-16-nov) · [TradingView connection check](#tradingview-connection-check-at-sign-in-added-16-nov)
14. [King pull scoreboard](#king-pull-scoreboard-added-16-nov) · [King analysis in the journal](#king-analysis-in-the-trading-journal-added-17-nov) · [Trade rules changed on 17 Nov](#trade-rules-changed-on-17-nov-effective-18-nov) · [Journal frozen days](#the-journal-is-written-during-the-session-finished-days-are-frozen-18-nov)
15. [Gamma nodes layer](#gamma-nodes-layer-the-nodes-agent-built-14-16-nov) · [Pages, templates and messages](#pages-templates-and-messages-where-each-lives) · [Telegram commands](#telegram-commands-your-private-chat-only) · [Change log](#change-log)

## Strategy rules (current, 18 Nov 2026)
This is the one place that lists every rule the paper-trade engine and the pages use. Constants are in `pipeline/stream_trades.py` unless another file is named. Times are IST. Everything here is a simulation in percentages of the option premium (no brokerage, taxes or slippage); the order router (below) is a separate, optional layer.

### 1. Levels and setups
- **Levels that give setups:** end-of-day GEX call wall and put wall, POC, VAH, VAL, weekly POC / VAH / VAL, and the daily and weekly VWAP. Max pain, OI walls, the gamma flip and the "Now" volume levels are drawn but give no setups (`NOW_VA_SETUPS` in `pipeline/stream_setups.py` is off because TradingView cannot reproduce them on past candles).
- **Setup rules** (`pipeline/stream_setups.py`, same as the Pine indicator): a candle touches a level, a doji forms at it, price leaves and retests, the next candle decides CALL (closes above) or PUT (closes below), tagged bounce / cross. A: a candle that closes through the level also counts as step 2. B: a doji counts within 12 points of the level. C (VWAP break): the previous 8 candles on one side of the VWAP, a candle closes through it, the next candle closes beyond it. D (VWAP bounce) is OFF since 10 Nov (`BOUNCE_ON`). A candle reports a level only once.
- **Where the setups come from:** `data/stream_relay.json` has `"signals_from": "tradingview"` (since 16 Nov): trades are booked from the setups TradingView's BP Live alert reported (read from TradingView's alert log). If the 09:05 TradingView check fails, the page books from its own setups that day (`data/signal_source_day.json`, one day only). Putting `"fyers"` in the file does the same permanently.

### 2. When a setup becomes a trade
Checked in this order; the first rule that applies becomes the setup's status and later rules are not looked at.
| # | Rule | Status text | Setting |
|---|---|---|---|
| 1 | Before 09:25 (first 10 minutes after the open) | `no trade before 9:25` | `EARLIEST` |
| 2 | A trade is already open (one trade at a time) | `ignored: trade N is open` | |
| 3 | 5 trades already taken today | `daily limit: 5 trades done` | `MAX_TRADES` |
| 4 | Closed trades of the day add up to -30% of premium | `daily loss limit reached (-30%)` | `MAX_LOSS_PCT` |
| 5 | After 14:45 | `no new trade after 14:45` | `CUTOFF` |
| 6 | You sent `/pause` on Telegram | `not taken: paused (you sent /pause)` | `data/trading_pause.json` |
| 7 | Negative gamma AND against the session trend (section 5) | `not taken: against the session trend in negative gamma (session trending up/down)` | `CONTRA_IN_NEGATIVE` |
| 8 | A node rule (all OFF by default, section 7) | `not taken: grade C is below the minimum grade B` / `a node sits just ahead of it (blocked)` / `in the middle of the range between two nodes` | `data/nodes_rules.json` |
| 9 | Entry trigger (section 3) | `waiting: buy only if the option trades above X` / `not taken: the option never crossed X (the setup candle's high) within 5 minutes` / `TRADE n` | `ENTRY_TRIGGER`, `ENTRY_WINDOW_MIN` |
Special sessions (Muhurat) keep neither the 10-minute wait nor any time rule. A setup with no option price is `no option price`.

### 3. Entry (entry trigger, since 16 Nov)
The trade is CALL = buy the at-the-money call, PUT = the at-the-money put, nearest weekly expiry (Fyers option candles). A setup does not become a trade at its candle's close. It becomes one only when the **option's** price trades above the **high of the option's setup candle** (a buy-stop) within **5 minutes**. Entry price = that high, or the next candle's open if it gaps above it. Stop, target and trailing are measured from the entry price. While waiting, the strip says "waiting", Telegram sends "SETUP WAITING"; if it never crosses, the setup is listed with the result the old entry (setup candle's close) would have given. `ENTRY_TRIGGER = False` goes back to the old entry.

### 4. Exit rules (one set all day since 18 Nov)
- **Target +40%, stop -10%** of the entry premium, **trailing stop**: once the premium has reached +20% the stop moves to +5%, at +30% to +15%, at +35% to +30% (never down). A candle is judged with the stop that was in force at its start; a candle that touches both the stop and the target (or a trail level) counts as the stop. A gap down through the stop fills at the open. A stop hit above the entry is a `TRAIL` (profit locked). A trade still open at 15:29 closes at that price (`DAY END`).
- **Regime tightening:** when the gamma regime turns against an open trade that is in profit (a PUT and the regime becomes POSITIVE, or a CALL and it becomes NEGATIVE), the stop moves up to 5% below the current premium (`REGIME_BUFFER`), never lower. The journal and Telegram show it as "stop moved ... because the gamma regime turned ...".
- **The afternoon set is off.** Until 17 Nov a trade entered from 13:00 used target +20%, stop -7% and trailing +10% to +5% / +15% to +10%. It was switched off at the owner's request (the whole set, not only the target); `AFTERNOON_RULES = True` brings it back. Journal days up to 17 Nov keep the rules they were taken under.

### 5. Negative gamma follows the session trend (since 18 Nov)
Negative gamma means directional moves, so the page first reads the session: UP when the index is at least `TREND_BAND_PTS` (10 points) above its **session VWAP** and above the **session open**, DOWN when at least 10 points below both, otherwise unclear (`session_trend` in `pipeline/stream_trades.py`; the live server fills it into `regime_at` in `tools/stream_server.py`). In negative gamma a CALL is skipped in a down session and a PUT in an up session; in positive gamma, or with no clear trend, nothing is skipped. (Before 18 Nov the test was whether price was above or below the gamma flip.) The regime itself is the live reading: POSITIVE where positive gamma applies on the side of the flip the price is on, NEGATIVE otherwise. The nodes agent's setup grade ("with the move / against the move") still uses the flip side and is information only.

### 6. "Would have been" (testing, never changes a day)
A setup kept out by a rule (before 09:25, against the session trend, node rules, never crossed, a daily limit) is run through the same entry, stop, target and trailing rules and its result is saved with it (`would_pct`). It shows as a tiny line on the live strip, in the hover card and in the journal ("If taken (testing)" column and the **Rules scoreboard**: taken trades by grade A / B / C, and for each rule what the setups it kept out would have returned). A rule is helping when what it keeps out does worse than what is taken.

### 7. Node rules (OFF)
`data/nodes_rules.json` (edit; no restart): `{"min_grade": null | "A" | "B" | "C", "skip_blocked": false, "skip_midpoint": false}`. Grades are saved with every setup and shown on the page; they only change a trade if you switch a rule on. Decide with the journal scoreboard and the reaction ledger after some weeks of data.

### 8. Limits, switches and files
| What | Where | Default |
|---|---|---|
| Entry trigger on / window | `ENTRY_TRIGGER`, `ENTRY_WINDOW_MIN` | on / 5 minutes |
| Afternoon rule set | `AFTERNOON_RULES` | **off** |
| Negative-gamma skip / trend band | `CONTRA_IN_NEGATIVE`, `TREND_BAND_PTS` | on / 10 points |
| Regime stop buffer | `REGIME_BUFFER` | 5% |
| Trades a day / loss limit | `MAX_TRADES`, `MAX_LOSS_PCT` | 5 / 30% |
| First trade / last new trade / last minute | `EARLIEST`, `CUTOFF`, `LAST_MINUTE` | 09:25 / 14:45 / 15:29 |
| Pause new entries | Telegram `/pause`, `/resume` (`data/trading_pause.json`) | resumed |
| Node rules | `data/nodes_rules.json` | all off |
| Where setups come from | `data/stream_relay.json` `signals_from`, `data/signal_source_day.json` | tradingview |
| Orders | `data/orders_config.json` `mode` | `off` by default (`dry_run` is set on this PC; `live` only by hand) |
Orders (optional router, section "Order router"): one lot (65), one position, entry not filled in 45 s is cancelled and not retried, partial fills protected, broker stop-limit order sized from the fill price, loss limit 5,000 rupees a day, at most 5 EXECUTED entries a day, nothing for a signal older than 3 minutes, square-off 15:15. It never runs unless `mode` is `live`.

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
- **Data:** Nifty candles from Fyers every 15 s in session; end-of-day levels read from the published dashboard page; live GEX every 5 minutes; "Now" volume levels (0.25-point rows, close to TradingView's own profile), daily and weekly VWAP.
- **Countdown timer:** in the centre of the chart whenever no session is running ("NSE opens in 02:34:10", "Market closed. Next session in 2D 07:31:53"). It follows the **NSE holiday calendar**: `pipeline/nse_calendar.py` reads NSE's official holiday list (cached in `data/nse_calendar.json`, refreshed about twice a day), skips holidays ("NSE holiday (Dussehra). Next session in ...") and counts special sessions such as Muhurat trading as sessions. NSE publishes the Muhurat timing late; until then the timer says "timing not announced yet" (Diwali Laxmi Pujan, Sun 8 Nov 2026) and it picks the times up by itself once NSE lists them. If you know them earlier, put them in `data/nse_special_sessions.json`, e.g. `{"2026-11-08": {"open": "18:15", "close": "19:15", "name": "Muhurat trading"}}`. The stream's fast polling, the broadcast start / stop and the 09:20 check use the same calendar.
- **Daily limits** (also in Strategy rules): after 5 trades in a day, or once the day's closed trades add up to -30%, no further setup becomes a trade (they are listed as "daily limit" / "daily loss limit reached"). Change `MAX_TRADES` and `MAX_LOSS_PCT` in `pipeline/stream_trades.py`.
- **Telegram trade messages**: during the live session the stream page sends one Telegram message for each new paper trade (side, strike, level, entry, target, stop), one when it closes (target / stop / day end with the percentage) and a short line for setups not taken. Events older than 3 minutes (a restart mid-session) are not repeated; the keys sent today are kept in `data/telegram_sent.json`. Every Telegram send attempt, including live level messages, is logged in `data/telegram_log.txt` (git-ignored).
- **Setups (CALL / PUT)**, the same rules as the Pine indicator, replayed on the day's candles. Original rule: a candle touches a level, a doji forms at it, price leaves and retests, the next candle decides CALL (close above) or PUT (close below), tagged bounce / cross. **A**: a candle that closes through the level also counts as step 2. **B**: a doji counts within 12 points of the level. **C, VWAP break**: the previous 8 candles on one side of the VWAP, a candle closes through it, the next candle closes beyond it. **D, VWAP bounce (switched OFF since 10 Nov 2026: `BOUNCE_ON` in `pipeline/stream_setups.py`, setting D in the indicator, default off)**: the previous 5 candles on one side, a candle touches the VWAP and closes back on that side, the next candle closes beyond it; at most one per 10 candles per VWAP. A candle reports a level only once. Levels that give setups: end-of-day GEX call / put wall, POC, VAH, VAL, weekly POC / VAH / VAL; daily and weekly VWAP. Now VAH / Now VAL give NO setups on the page (switch `NOW_VA_SETUPS` in `pipeline/stream_setups.py`) because the TradingView indicator cannot give them on past candles; the page and the chart now use the same levels.
- **Paper trade tracker** (percentages only, no orders): every rule (entry trigger, target +40% / stop -10% / trailing, regime and session-trend rules, daily limits, 09:25 start, 14:45 cutoff, would-have results) is listed in **Strategy rules (current)** at the top. The trade strip shows every trade of the day (earlier ones as chips) and the open or latest one in detail, with the day total; setups that confirm while a trade is open are only listed ("ignored: trade N is open"). No brokerage, taxes or slippage.
- **After the close** the page keeps showing the day's trades: the levels that were valid during the session are saved in `data/stream_eod/` (one file per close date) and replayed, and the option prices come from the session's own day (or from the journal if Fyers no longer has them).
- **Opening candles from TradingView:** Fyers' 09:15 candle differs from TradingView's (open, high and above all volume) and that candle dominates the VWAP. Your TradingView sends its own 09:15 to 09:30 candles through a webhook alert and the free ntfy.sh relay (`python tools/stream_relay_setup.py`, steps in `tradingview/LIVE-SETUP.md`); the page uses them for the opening and Fyers afterwards, and keeps them per day in `data/stream_open/`. The header shows "opening candles: TradingView (15)".
- **Addresses:** `http://127.0.0.1:8766/` is the full view for your own screen. `http://127.0.0.1:8766/?clean=1` hides the setups, trades, profit and loss and markers (the OBS / YouTube source now uses `?theme=dark` WITHOUT clean, so the trades, in percentages, are shown on the stream; add `&clean=1` in `data/obs.json` to hide them again). `&theme=dark` or `&theme=light` picks the background (default light).
- If the feed stops the page says DATA STALE. YouTube viewers are always 10 to 30 seconds behind (set Low latency in Studio to shorten it).

## OBS and YouTube
`tools/obs_control.py` runs inside the stream program (only when `data/obs.json` exists). It opens OBS minimised, makes sure the scene **BharatPulse** with a Browser source `http://127.0.0.1:8766/?theme=dark` (1920x1080) exists, starts the broadcast and stops it at 15:35 IST, or earlier if the feed has been bad for 10 minutes (then it stays off until the next day, and a broadcast you stop yourself is not restarted). Before starting it selects the BharatPulse scene and reloads the page; it never changes your other scenes. After StartStream it checks that OBS is really sending and tells you on Telegram if not (one attempt a day, so OBS's error window does not pop up repeatedly).
- **Modes** (`data/obs.json`, set with `python tools/obs_setup.py [direct|at_open|off]`): **direct** (starts right after your sign-in; the chart shows the countdown until the open), **at_open** (starts at 09:31 IST once the feed is healthy), **off** (only opens OBS; use the helper page's buttons). Currently **direct**.
- **One-time setup:**
  1. OBS: Tools > WebSocket Server Settings > enable it (OBS made its own password; it listens on the network, so keep the password strong). The setup script saved it in `data/obs.json` (git-ignored).
  2. OBS: Settings > Stream > YouTube - RTMPS > **Use stream key** and paste the key from YouTube Studio (Stream page, "Default stream key"). Do not rely on "Connect Account": an automatic start then fails with "rtmp_output failed to start" because OBS has no key. Keep the key private.
  3. YouTube Studio: enable live streaming on the channel (can take up to 24 hours after you request it).
- **Sharing: Unlisted, you paste the link each day.** Every OBS go-live creates a new broadcast with a new link, and Private invitations do not carry over. Set Studio > Stream > Edit > Visibility **Unlisted** (anyone with the link can watch; not listed on the channel or in search; ask friends not to forward it). Turn live chat off, set **Low latency**, and put "not SEBI registered, not financial advice, education only" in the title or description. (With Private instead, every broadcast needs each Google account invited again, and the invited person must be signed in with exactly that account.)
- A test broadcast was run successfully on 2026-11-07 (the stream reached YouTube).
- Check SEBI rules (investment advice / research analyst registration) and the NSE, Fyers and TradingView data-redistribution terms before sharing widely: a disclaimer banner does not replace registration or a data licence.

## Daily snap video (56 seconds, vertical, for mobile)
`tools/make_snap.py` turns the day's dashboard data into a 56-second 1080x1920 video (H.264, 30 fps). It is made automatically after the evening build (the helper waits about 2.5 minutes for the dashboard page to update, then runs it and sends the video to your Telegram for a quick look), or any time with the helper page's **Make today's snap video now** button, or `python tools/make_snap.py [--telegram] [--preview] [--source local] [--music FILE | --no-music]`. Nothing is uploaded: you upload it yourself (YouTube Shorts, Instagram Reels, WhatsApp status).
- **Scenes** (10 scenes, each with a chart; exact times in `timing.txt`): hook with a 52-week Nifty line 0-4 s, indices with 1D to 1Y return bars 4-10 s, breadth history, VIX line and P/E range gauge 10-16 s, all-sector heat map 16-22 s, Nifty 50 biggest movers 22-28 s, momentum leaders with 6-month sparklines 28-35 s, FII/DII flows and global markets 35-41 s, something unusual (three automatic insights) 41-46 s, levels to watch with the gamma-exposure profile 46-52 s, and the dashboard link (abhijitkikale.github.io/bharatpulse) 52-56 s. A "not SEBI registered, not financial advice, education only" line stays on screen all the time. The lowest part of the frame is left empty on purpose so the Reels/Shorts buttons do not cover the text.
- **Insights** are scored from the data and worded from fixed templates (facts only, never "buy", "sell" or "target"): breadth against the index, smallcaps against the Nifty, a sector far from the Nifty, FII / DII flows and streaks, India VIX moves, a big move on very large volume, the gamma regime against the flip (and a regime change from yesterday), 52-week highs / lows counts, and sector momentum-rank moves (from the second day on, using `data/snap_history/`).
- **Files** in `data/snap/YYYY-MM-DD/`: `snap_music.mp4` (with the music) and `snap.mp4` (silent), `cover.png` (thumbnail), `caption.txt` (title, description, hashtags, disclaimer and the music credit), `timing.txt`, `snap.json` (the day's summary). Music: the first `.mp3` in the project folder is used (currently "Langoos" by Moavii from Free To Use; keep the credit line in the description and check the licence terms on freetouse.com). The music file is not part of the repository.
- Made with headless Chrome (Playwright) frame by frame and `imageio-ffmpeg` (`pip install imageio-ffmpeg`); about 90 seconds on this PC. Holidays and weekends: run it only after a real session.

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
| `nodes/` | the nodes agent: `latest.json`, `YYYY-MM-DD.jsonl` (readings every 2 minutes), `grades_YYYY-MM-DD.json`, `ledger.csv` |
| `journal_settings.json`, `journal_notes.json`, `nodes_rules.json` | journal capital and lot, your own notes per day, node rules (all off) |
| `orders_config.json`, `orders/`, `fyers_order_token.json` | order router settings, its daily logs and its own token (never sent to GitHub) |
| `tv_status.json`, `tv_inbox.jsonl`, `tv_paste_log.txt`, `signal_source_day.json`, `trading_pause.json` | TradingView check, alert-log mailbox, paste log, one-day fallback of the signal source, `/pause` state |
| `snap/YYYY-MM-DD/`, `snap_history/` | the daily snap video, cover, caption, timing sheet and summary; the small daily record the next video compares with |

## Other commands, data, notes
- Tests: `python -m pytest tests` (the OBS tests skip when the websocket libraries are not installed, which is the case on GitHub's build machine; the cloud build runs the tests first, so a test that needs a missing library blocks publishing).
- Rebuild from cached data only: `cd pipeline && python run.py --cached`.
- Older local route: `run_daily.bat` / `install_schedule.bat` (scheduled local refresh); the cloud build replaces it.
- Valuation: trailing P/E, P/B, yield from NSE; forward P/E from Yahoo analyst consensus, recorded daily in `data/fwd_pe.csv`.
- Sharing the dashboard file: Google Drive shows HTML as text; download `dashboard.html` and open it locally.
- Data: NSE archives (indices, constituents, F&O bhavcopy, participant OI, FII/DII, holiday list), Yahoo Finance (stocks, global indices, FX), Fyers data-only API (1-minute index candles with index volume, option chain and option candles). Private Bank and Capital Market constituents are approximations (NSE publishes no file).

Educational/informational only - not investment advice. Market data is from NSE, Yahoo Finance, Fyers and TradingView and is subject to their terms.


## Live GEX text pasted into TradingView automatically

`tools/live_loop.py` saves the newest live text to `data/live_levels_NIFTY.txt` every 5 minutes. `tools/tv_live_paste.py` keeps its own hidden Google Chrome
(profile `data/tv_profile`, separate from your normal Chrome) open on your chart (`data/tv_chart.json`) and pastes that text into the 'Live levels' box of the BP Live indicator
whenever it changes (09:10-15:40 IST). One time: `python tools/tv_live_paste.py --login` (sign in with the 2-step code in the window; it closes by itself).
`Refresh BharatPulse.bat` starts the pasting after the Fyers sign-in. Test: `python tools/tv_live_paste.py --once`; results: `data/tv_paste_log.txt`.

## Trading journal page

`Open Trading Journal.bat` (or `python pipeline/journal_dashboard.py --open`) opens `data/journal_dashboard.html`: every journaled day with its paper trades in rupees (one lot of 65 on a 25,000 capital, change in `data/journal_settings.json`), the setups taken or ignored, the index chart and the option price of each trade. It is rewritten at every journal save and is also served at `http://127.0.0.1:8766/journal` while the helper runs. It stays on your PC (the `data` folder is not uploaded).

## Telegram for friends, and control from your phone

* **Friends' channel** (`tools/telegram_channel_setup.py`, or double-click `Connect Telegram Channel.bat`): create a Telegram channel, add GEXINDICATORBOT as an administrator, run the script once. The channel then gets the public messages only: live GEX levels (blue), trade taken with entry / target / stop-loss (orange), target hit (green), stop-loss hit (red), closed some other way (yellow). Setups not taken, warnings, videos and the raw indicator text stay in your own chat. Friends join with the channel's invite link and can only read.
* **Commands to the bot** (`tools/tg_control.py`, answered only for your own private chat): `/status`, `/stream_on`, `/stream_off`, `/sleep`, `/shutdown` (each of the last two needs `/confirm` within 60 seconds, `/cancel` stops it), `/help`. Old commands are never run later.

## Order router (automatic orders): OFF by default

`pipeline/order_manager.py` + `tools/order_router.py` turn the stream page's trades into broker orders: BUY limit just above the reference price, a broker stop-loss (stop-limit) order as soon as it fills, the stop order is changed at every trailing / regime stop move, and the position is exited when the page says the trade is over (or at 15:15). One position at a time; a failed entry is cancelled, never chased; a failed stop order exits at once; nothing new after the day's loss limit or after 5 executed entries. Every step goes to your private Telegram chat and to `data/orders/<day>.log`.

`data/orders_config.json` (all optional, shown with the defaults): `{"mode": "off", "lots": 1, "lot_size": 65, "max_lots": 1, "entry_slip_pct": 2.0, "exit_slip_pct": 2.0, "sl_gap_pct": 1.5, "entry_wait_s": 45, "max_daily_loss_inr": 5000, "max_entries_per_day": 5, "max_api_orders": 60, "square_off": "15:15", "max_signal_age_min": 3}`. The quantity is always exactly one lot (65) whatever the file says. `max_entries_per_day` counts EXECUTED entries (filled BUY orders); `max_api_orders` is only a runaway guard on order requests and never blocks a stop or an exit.

1. **mode `"dry_run"` first.** The whole logic runs against a pretend broker and Telegram tells you what it WOULD do ("[DRY RUN] trade 1: BUY 65 ... limit ...", "Stop-loss order placed ...", "stop-loss order moved ..."). No order leaves the PC and no Fyers order app is needed. Run it for a few sessions and compare with what you would have done.
2. **mode `"live"`** (you edit the file at the PC; there is deliberately no Telegram command for it): needs the Fyers **Algo Trading** app activated at myapi.fyers.in (registered fixed IP = the address this PC reaches Fyers from, redirect URL `http://127.0.0.1:5051/`), the environment variables `FYERS_ORDER_APP_ID` / `FYERS_ORDER_APP_SECRET`, and a sign-in each morning with `Order Sign-in.bat` (token in `data/fyers_order_token.json`, never sent to GitHub). Without a valid token the router sends nothing and says so once.
3. **Kill switch:** Telegram `/orders_off` (nothing new is sent; a stop-loss order already at the broker stays, check the Fyers app) or `/orders_dry`. `/pause` stops new entries only.

`FyersBroker` is written from Fyers' API v3 documentation and has never been run against the real service by its author: the first live order must be small and watched with the Fyers app open. The page reads 1-minute candles, so the broker-side stop-loss order is what actually protects you. Not investment advice; you are responsible for every order.

## Entry confirmation and the TradingView alert log (added 16 Nov)
- **Entry trigger:** a setup is no longer bought at the close of its candle. It becomes a trade only when the **option's** price trades above the high of the setup candle (a buy-stop at that high), within 5 minutes (`ENTRY_WINDOW_MIN` in `pipeline/stream_trades.py`; `ENTRY_TRIGGER = False` switches it off). Entry price = that high (or the next candle's open if it gaps above); stop, target and trailing are measured from it. While it waits, the page strip shows "waiting: buy only if the option trades above X" and Telegram sends "SETUP WAITING"; if it never crosses, the setup is "not taken: the option never crossed X" and the journal scoreboard shows what the old entry would have returned.
- **TradingView alert log instead of the relay:** the hidden browser (`tools/tv_live_paste.py`) reads TradingView's own alert log (`POST pricealerts.tradingview.com/list_fires`) every 20 seconds and hands each new BP Live message to the page through `data/tv_inbox.jsonl`, so the relay's 429 rejections no longer lose opening candles or setups. The ntfy relay still works as a second path (duplicates change nothing).
- **Fyers request budget:** the Fyers plan allows 5,000 requests a day for everything; `/status` and the 09:31 message show the count; the page polls every 15 s, option candles are refreshed often only for an open trade, and the nodes agent reads the page's candles.

## TradingView connection check at sign-in (added 16 Nov)
Right after your Fyers sign-in the hidden TradingView browser checks, from TradingView's home page (it never opens the chart, so it cannot take the chart session from your own TradingView): signed in, BP Live alert present, alert log readable, published end-of-day levels from the last trading day. The result goes to **Telegram** ("TradingView check 07:32 IST: ALL OK", or "PROBLEM, fix before you leave" with what to do for each problem), to a card on the **helper page**, and to a chip on the **live page** (green "TradingView ✓" / red "TradingView: PROBLEM", the tooltip lists every line). It is re-checked every 10 minutes while there is a problem, finally at 09:05 IST; if the 09:05 check still fails, **the page books from its own setups for that day** (`data/signal_source_day.json`, never carried to the next day) and Telegram says so. Later the same status keeps the alert renewal, a taken chart session and the alert-log reader's heartbeat; a failed renewal (3 tries), a taken session and a silent reader each send one Telegram message. If the hidden browser has not reported 2.5 minutes after the sign-in, the helper sends a Telegram warning. `TradingView Sign-in.bat` signs the hidden browser in again.

## King pull scoreboard (added 16 Nov)
The node replay page (`Open Node Replay.bat`, or http://127.0.0.1:8766/nodes-replay) now opens with a scoreboard that asks one question: does price drift toward the King more than toward any other level? For every saved nodes reading it checks whether price was closer to the King 15, 30 and 60 minutes later, by how many points, and whether it touched it. The same is asked of controls: the King moved 50 and 100 points either way, the day's VWAP, the range midpoint and the nearest round hundred. It also splits by how far the King is, days to the weekly expiry (Tuesday), regime, King sign and time of day. The verdict line stays at "too early to say" until there are about 30 independent windows (a day gives roughly 12; neighbouring readings overlap and do not count as separate evidence), and it only says a pull shows when the King beats every control by a clear margin. Code: `pipeline/nodes_pull.py`; it fills itself from the nodes agent's saved days, nothing to switch on.

## King analysis in the trading journal (added 17 Nov)
Each day's page in the journal (`Open Trading Journal.bat` or http://127.0.0.1:8766/journal) now has a King analysis under the setups: the King strike through the day drawn on the chart (amber thick line = positive King, violet = negative), a table of each King that lasted about 6 minutes or more (strike, from and to, price at start and end, points toward the King, the King's candle with its open/high/low/close, and what came next), the day's drift toward the King against controls, whether each taken trade pointed toward or away from the King, and where the day closed relative to it. The home page has a "King pull" panel with all days together. Your own notes go in `data/journal_notes.json` as `{"2026-11-17": ["a paragraph", ...]}` and show under that day's King analysis. The analysis is rebuilt from the nodes agent's saved readings (`pipeline/king_journal.py`); a day without saved readings simply shows no King section.

## Trade rules changed on 17 Nov (effective 18 Nov)
- **One set of rules all day:** target +40%, stop -10%, trailing +20% to +5%, +30% to +15%, +35% to +30%. The tighter afternoon set (from 13:00: target +20%, stop -7%) is switched off; `AFTERNOON_RULES = True` in `pipeline/stream_trades.py` brings it back. Trades in the journal up to 17 Nov keep the rules they were taken under.
- **Negative gamma follows the session trend:** negative gamma means directional moves, so the page first reads the session. It counts as trending UP when the index is at least 10 points above its session VWAP and above the session open, DOWN when at least 10 points below both, otherwise unclear. In negative gamma a CALL is skipped in a down session and a PUT in an up session; with no clear trend, nothing is skipped. Before, the test was whether price was above or below the gamma flip. The skip shows in the journal as "Against the session trend in negative gamma (session up / down)" with its testing result.

## The journal is written during the session; finished days are frozen (18 Nov)
The stream server (started by the helper) writes `data/journal/YYYY-MM-DD.json` and rebuilds `data/journal_dashboard.html` during the live session, at most once a minute, so the last write is the last change of the day (about 15:30 IST); there is no separate end-of-day job, and the helper window has to stay open until 15:45. A day that is already over and on disk is now **frozen**: starting the helper on a later morning, or changing a rule, no longer re-simulates it (on 18 Nov that had overwritten the 17 Nov record; it was restored). To replace a past day on purpose, `stream_journal.write(doc, folder, force=True)`. Each day's page now carries the full King graph (King line, floor and ceiling, negative-gamma shading, numbered node reactions) under the setups.

## Gamma nodes layer (the "nodes agent", built 14-16 Nov)
Ideas from Skylit's Heatseeker and Atlas, built for Nifty. Design and phases: `docs/superpowers/specs/2026-11-14-nodes-agent-design.md`. A node is a strike where the gamma exposure of the option chain is large; "context, not a trigger" means a node tells you where price may react and how likely, while the setup rules above decide when.
- **How it is computed** (`pipeline/nodes.py`, pure functions; `pipeline/gex.py` does Black-76 with implied volatility from prices and the forward from put-call parity): for the nearest 4 expiries, value of a strike = call GEX (+) plus put GEX (-), in rupees crore per 1% move. In the nearest expiry ("Front" column): **strength** = |value| as a share of the largest; **significant** = at least 25% (`SIG_PCT`); **King** = the largest |value|; **gatekeeper** = the largest significant node between price and the King; **floor / ceiling** = the nearest significant node below / above price; **pika / barney** = the biggest positive / negative nodes; **air pocket** = 3 or more strikes under 10% of the King; **midpoint** = middle of the floor-ceiling range; **decoy** flag; **touches** (candle range within 5 points of the strike) split nodes into fresh and delivered; **velocity** over 5 and 15 minutes (building / melting); **regime** from the sign of the Front column's total gamma. Expected move: 1 sigma = previous close x front at-the-money IV x sqrt(1/365), drawn with 2 sigma.
- **Setup grade A / B / C with reasons** (information only): backing node on the protected side within 30 points (+2 from 60% strength, +1 from 25%), room to the next node (+1 from 50 points, -1 under 25, "blocked" flag), freshness of the backing node (+1 untouched, -1 touched 3 or more times), confluence with 2 or more other levels within 10 points (+1), regime (negative gamma with the move +1, against it -2, flag `contra`), middle of the range (-1, flag `midpoint`), no significant node within 40 points (-1, flag `nonode`). A >= 3, B 1 to 2, C <= 0. The first grade of a setup is kept (`data/nodes/grades_<day>.json`) and marked late if the snapshot is more than 3 minutes newer than the setup.
- **The agent** (`tools/nodes_agent.py`, started by the helper in its own process, lock file so never twice): a snapshot every 120 seconds in market hours from the Fyers chain (4 requests each), reading candles and levels from `data/stream_state.json` (it makes no candle request of its own, to save the 5,000 requests a day Fyers allows for everything). It writes `data/nodes/latest.json`, the day's readings `data/nodes/YYYY-MM-DD.jsonl` (the recorder everything below is built from) and the grades. The page works without it.
- **Live page:** orbs on the chart for the strongest nodes (size = share of the King, yellow positive, purple negative, King starred, a marker at the chart edge when it is off the visible range, trade markers offset so they do not cover the chart), a heatmap panel (strikes x next expiries, floating so it does not cover the chart), grade badges on the setup chips, a one-line trade strip (waiting status, would-have result), a hover card, approach pop-ups (price within 15 points of a key node, and a second when it touches it; re-armed 40 points away), expected-move lines. `/?nodes=0` hides the layer.
- **Dashboard (end of day):** the Options tab has a "Tomorrow's nodes" card built after the close from the same chain (`pipeline/eod_nodes.py`).
- **Reaction ledger** (`pipeline/nodes_ledger.py`): every approach of price to a significant node (a candle range within 15 points, after price was away; re-armed 40 points away; the opening minutes before 09:20 skipped) is judged over the next 30 minutes: **held** (price went 25 points back the way it came), **passed** (25 points through) or **chop**, with the node's features (strength, role, sign, fresh or touched, regime, building or melting, time of day). `summarize()` gives hit rates per feature and the dealer-sign check (do positive nodes hold more than negative ones?). `data/nodes/ledger.csv`.
- **Node replay page** (`pipeline/nodes_replay.py`, `Open Node Replay.bat`, or http://127.0.0.1:8766/nodes-replay): pick a saved day, drag the slider or press play to see the nodes at each reading, the day's price with every reaction marked, the **King pull scoreboard** and the ledger tables.
- **King pull scoreboard** (`pipeline/nodes_pull.py`): does price drift toward the King more than toward any other level? Per reading and per horizon (15, 30, 60 minutes): was price closer to the King later, by how many points, did it touch it; the same for controls (the King moved 50 and 100 points either way, VWAP, range midpoint, nearest round hundred); splits by King distance, days to the Tuesday expiry, regime, King sign and time of day. The verdict says "too early" until about 30 independent windows (a day gives about 12). First two days (16 and 17 Nov): 77% then 43% closer at 30 minutes, 58% together against 48% for the best control, not yet a verdict.
- **King in the trading journal** (`pipeline/king_journal.py`): see "King analysis in the trading journal" below.
- **Private explainer** (not in the repo): a plain-language page of what was built from Skylit, with a real day's chart, that can be shared with friends from the Artifacts Share menu.

## Pages, templates and messages (where each lives)
| What | Template / code | Where you see it | Fed by |
|---|---|---|---|
| End-of-day dashboard (tabs, Options tab with GEX, levels text for TradingView, Tomorrow's nodes card) | `pipeline/template.html`, `pipeline/build.py`, `pipeline/run.py` | `dashboard.html`, GitHub Pages (cloud build `.github/workflows/refresh.yml`) | NSE files, Yahoo, Fyers |
| Live stream page (chart, levels table, strip, setups, nodes layer, TradingView chip) | `tools/stream_page/index.html` | http://127.0.0.1:8766/ (`?clean=1`, `?theme=dark`, `?nodes=0`) | `tools/stream_server.py` |
| Trading journal (calendar, stats, rules scoreboard, King pull panel, day pages with the King graph) | `pipeline/journal_dashboard.py` (TEMPLATE) + `pipeline/king_journal.py` | `Open Trading Journal.bat`, `data/journal_dashboard.html`, http://127.0.0.1:8766/journal | `data/journal/*.json`, `data/nodes/*.jsonl`, `data/journal_notes.json` |
| Node replay, reaction ledger, King pull scoreboard | `pipeline/nodes_replay.py` (PAGE) | `Open Node Replay.bat`, http://127.0.0.1:8766/nodes-replay | `data/nodes/*.jsonl`, journal candles |
| Helper page (sign-in, live status, TradingView card, OBS card) | `tools/refresh_app.py` | http://127.0.0.1:5050/ (`Refresh BharatPulse.bat`) | |
| Telegram messages (trade taken with order-ticket line, STOP MOVED, EXIT, SETUP WAITING, levels, checks) | `pipeline/stream_alerts.py`, `tools/tg_style.py`, `tools/telegram_notify.py` | your chat; the friends' channel gets the public kinds only | |
| Snap video, OBS / YouTube | `tools/make_snap.py`, `tools/obs_control.py` | | |
| TradingView indicators | `tradingview/*.pine` | | text pasted automatically (`tools/tv_live_paste.py`) |
Design documents: `docs/superpowers/specs/`.

## Telegram commands (your private chat only)
`/status` (stream, nodes agent, Fyers request count, TradingView feed, order mode), `/pause` and `/resume` (new entries only), `/orders_off` and `/orders_dry` (there is deliberately no command for live), `/stream_on`, `/stream_off`, `/sleep` and `/shutdown` (each needs `/confirm` within 60 seconds, `/cancel` stops it), `/help`. Old commands are never run later.

## Change log
| Date (2026) | Change |
|---|---|
| 10 Nov | VWAP bounce (setup D) switched off; Now VAH / VAL give no setups |
| 13 Nov | No trade before 09:25; trailing stop (+20% to +5%, +30% to +15%, +35% to +30%) |
| 13-14 Nov | Regime rules: skip trades against the move in negative gamma, tighten the stop when the regime turns against a profitable trade; afternoon set from 13:00 (target +20%, stop -7%) |
| 14 Nov | Nodes agent phases 1-3: orbs, heatmap, grades, approach pop-ups, expected move, node rules file (off), would-have results, reaction ledger, node replay page, dealer-sign check |
| 14-15 Nov | Going live: order ticket in Telegram, STOP MOVED / EXIT messages, `/pause`, "Tomorrow's nodes" card, order router in dry run (one lot, 5 executed entries a day) |
| 16 Nov | Fyers 5,000-requests-a-day cap hit at 14:09: request counter, 15 s polling, nodes agent reads the page's candles; TradingView alert-log reader replaces the lossy relay; entry trigger (buy-stop above the setup candle's high, 5 minutes); trades booked from TradingView signals; TradingView connection check at sign-in; King pull scoreboard |
| 17 Nov | King analysis in the journal; rule changes decided |
| 18 Nov | **One exit rule set all day (afternoon set off); negative gamma skips trades against the session trend**; finished journal days frozen (a restart had overwritten 17 Nov, restored); full King graph per journal day; the cloud build's history-file push can no longer stop publishing |
