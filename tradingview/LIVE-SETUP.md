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
- **D: VWAP bounce signal**: the previous 5 candles (setting) were all on one side of the VWAP, a candle touches it and closes back on that side, and the next candle closes beyond it (CALL from above, PUT from below). Labelled "(VWAP bounce)". At most one per 10 candles (setting) on the same VWAP. A candle never reports the same level twice (retest, break and bounce share that rule).
- The same rules are replayed by the stream page (`tools/stream_server.py`), so its setups table and trades match this indicator; only the data feed differs (Fyers candles after 09:30, TradingView's own opening candles through the alert below).

## One-time Telegram setup (on the PC that runs Refresh BharatPulse.bat)
1. Open https://t.me/GEXINDICATORBOT in Telegram and press Start.
2. In the BharatPulse folder run `python tools/telegram_setup.py`, paste the bot token from @BotFather (right-click pastes; it is shown on screen) and press Enter. It saves the token and your chat id only in `data/telegram.json` on this PC (not published) and sends a test message.

## Opening candles from TradingView (for the stream page)
The stream page's Fyers feed has a slightly different 09:15 candle from TradingView's (open, high and above all volume). That candle dominates the VWAP, so VWAP-based setups near the open can differ from your TradingView chart. To fix it, your TradingView sends its own opening candles to the stream program every morning; the page uses them for 09:15 to 09:30 (as sent) and Fyers afterwards.

One-time setup (needs a TradingView plan with webhook alerts, and two-factor sign-in enabled on your TradingView account):
1. On the PC that runs the helper: `python tools/stream_relay_setup.py`. It makes a private topic for the free ntfy.sh relay and prints the webhook URL (`https://ntfy.sh/<topic>`). Keep that URL private. Only the opening 1-minute Nifty candles go through it, about 15 messages a day.
2. In TradingView open **NSE:NIFTY on a 1-minute chart** with the live indicator on it. In the indicator settings tick **Send the opening 1-minute candles to the stream program** (group "Stream feed (webhook alert)"; the default is the first 15 minutes after 09:15).
3. Create an alert (clock icon): **Condition** = BharatPulse Levels Live > **Any alert() function call**; **Notifications** > tick **Webhook URL** and paste the URL from step 1; leave the message as it is (the indicator supplies it). Expiration: as long as your plan allows (TradingView alerts expire; recreate when it does). Create.
4. Restart the helper so it listens (it starts listening when the stream starts). After 09:16 the page shows "opening candles: TradingView (N)" in the header. If it does not, the alert is not firing or the topic differs. At 09:31 IST the Telegram READY message also says whether the TradingView opening candles arrived.
5. The program saves the candles it receives per day in `data/stream_open/` (the relay itself forgets them after 12 hours and accepts messages of the last few days only), and replays a day's session with that day's candles, also the next morning and after the evening build.
6. Alerts expire on TradingView: if the header stops showing "TradingView" and Telegram says "NOT received", create the alert again.

The connector used inside Claude cannot create webhook alerts, so this alert has to be made by you on tradingview.com.
