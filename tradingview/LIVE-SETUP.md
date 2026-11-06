# Live levels in TradingView

File: `BharatPulse-Levels-live.pine` (Rev 0 and Rev 1 stay as they are). Pine v6: copy the whole file into the Pine Editor, Save, then update the chart. TradingView does not update a pasted copy by itself.

## The two paste boxes (Settings > Inputs)

| Box | What you paste | When |
|---|---|---|
| **Levels (paste from BharatPulse)** | the end-of-day text from the dashboard (Options tab > Copy levels) | once, each evening |
| **Live levels (paste from Telegram)** | the newest message from the bot `GEXINDICATORBOT` | whenever Telegram tells you a level moved |

Select all in the live box and paste over the old message. The end-of-day lines never change during the day. Live lines are cyan, start at the time of the message and carry its time in the label.

## What the bot sends
Only when a live level has moved by at least one strike (50 points for Nifty), current expiry, **Nifty only** for now: Gamma flip, GEX call wall, GEX put wall, Max pain (the OI walls are end-of-day only), plus the regime lines. Long-press (phone) or select (laptop) the grey block, copy, paste into the live box.

## Lines drawn
- **End-of-day** (from the first box): spot, max pain, gamma flip, OI / GEX walls, POC, VAH, VAL, weekly POC / VAH / VAL (as in Rev 1).
- **Live** (second box): cyan, prefix "Live".
- **Now** (worked out by the indicator, cyan, prefix "Now", updated every candle from the chart's own candles and volume): Now POC / VAH / VAL (today so far), Now Weekly POC / VAH / VAL (last 5 sessions), **Now VWAP** (daily, restarts each session) and **Now Weekly VWAP** (yellow, restarts each Monday, as TradingView's weekly VWAP). The VWAPs are drawn as curves.
- Volume rows: **0.25 point** by default, which matched TradingView's own volume profile to within about a point (VAH / POC / VAL); 0 = the dashboard's 5 / 10 point rows. Use a 1, 3 or 5 minute chart. Without volume the label says "(no volume: candle count)".

## Regime box
One small box, two lines: `EOD: POSITIVE/NEGATIVE gamma` (from the end-of-day text) and `NOW: POSITIVE/NEGATIVE gamma (time)` (price against the live flip, from the Telegram message). No pop-ups when price crosses the flip. Default position top centre; the setups table is top right.

## Setups (CALL / PUT)
- Setups come only from the levels ticked under **Setups (touch > doji > retest) on these levels**. Default on: GEX call wall, GEX put wall, POC, VAH, VAL, weekly POC / VAH / VAL, daily VWAP, weekly VWAP, Now VAH, Now VAL (Now VAH / VAL work on candles that form live, not on past candles). Default off: spot, max pain, gamma flip, OI walls.
- **Live Telegram levels and the other Now lines are reference only.**
- Original rule: a candle touches the level, a doji forms at it, price leaves and retests; the next candle decides CALL (close above) / PUT (close below), tagged bounce / cross.
- **A**: a candle that closes through the level also counts as step 2 (no doji needed). **B**: a doji counts within 12 points of the level (setting). These run alongside the original rule, so they only add setups.
- **C: VWAP break signal**: the previous 8 candles (setting) were all on one side of the VWAP, a candle closes through it and the next candle closes beyond it. No retest needed. Labelled "(break)". Daily and weekly VWAP each have a tick box.

## One-time Telegram setup (on the PC that runs Refresh BharatPulse.bat)
1. Open https://t.me/GEXINDICATORBOT in Telegram and press Start.
2. In the BharatPulse folder run `python tools/telegram_setup.py`, paste the bot token from @BotFather (right-click pastes; it is shown on screen) and press Enter. It saves the token and your chat id only in `data/telegram.json` on this PC (not published) and sends a test message.
