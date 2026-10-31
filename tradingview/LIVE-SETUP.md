# Live levels in TradingView

File: `BharatPulse-Levels-live.pine` (Rev 0 and Rev 1 stay as they are).

## The two paste boxes (Settings > Inputs)

| Box | What you paste | When |
|---|---|---|
| **Levels (paste from BharatPulse)** | the end-of-day text from the dashboard (Options tab > Copy levels) | once, each evening |
| **Live levels (paste from Telegram)** | the newest message from the bot `GEXINDICATORBOT` | whenever Telegram tells you a level moved |

Select all in the live box and paste over the old message. The end-of-day lines never change during the day. Live lines are cyan,
start at the time of the message and carry its time in the label.

## What the bot sends

Only when a level has moved by at least one strike (50 points Nifty, 100 Bank Nifty), one message per index, current expiry only:
Gamma flip, GEX call wall, GEX put wall, OI call wall, OI put wall, Max pain.
Long-press (phone) or select (laptop) the grey block, copy, paste into the live box.

## What the indicator works out itself (cyan, prefix "Now")

Now POC / VAH / VAL (today so far), Now VWAP, Now Weekly POC / VAH / VAL (last 5 sessions), updated every candle from the chart's own
candles and volume. Same method as the dashboard: 5-point bins (Nifty) / 10-point bins (Bank Nifty), volume spread over each
candle's high-low range, 70% value area. Use a 1, 3 or 5 minute chart. Without volume on the chart the label says
"(no volume: candle count)".

## One-time Telegram setup (on the PC that runs Refresh BharatPulse.bat)

1. Open https://t.me/GEXINDICATORBOT in Telegram and press Start.
2. In the BharatPulse folder run `python tools/telegram_setup.py`, paste the bot token from @BotFather (hidden), press Enter.
   It saves the token and your chat id only in `data/telegram.json` on this PC (not published) and sends a test message.
