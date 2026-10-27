# BharatPulse Levels: TradingView publishing text

## Title (max 64 characters)
BharatPulse Levels: Daily Option Levels, Gamma Regime & Retest Setups

## Short description (one line, for the script card)
Paste your own daily option-derived levels (spot, max pain, gamma flip, OI/GEX walls, POC, VAH, VAL), see the day's gamma regime, and get touch > doji > retest CALL/PUT setups.

## Full description (paste into the publish box)

**What it does**
BharatPulse Levels draws a set of horizontal price levels that you supply once a day, and it adds three things on top:

1. **Levels with tidy labels.** Each level is drawn as a coloured line with a label on the right side of the chart. Labels that fall on the same price are stacked one below the other so every one stays readable.
2. **A regime box (top right).** If your pasted text includes a short regime description (for example "POSITIVE GAMMA REGIME" and what it means for the next session), it is shown in a box at the top right of the chart, green for positive and red for negative gamma.
3. **Touch > doji > retest setups (small table, position selectable).** For every enabled level the script looks for this sequence: a candle touches the level, a doji candle forms at the level, and a later candle retests the level after price has left it. If the retest candle closes ABOVE the level it is listed as a CALL setup, if it closes BELOW the level as a PUT setup. Each setup shows the time, the side, the option strike (the retest candle's close rounded to the nearest 50 for Nifty or 100 for Bank Nifty; step is configurable) and the level that triggered it. Small CALL/PUT markers appear on the chart, and alerts are available.

**How to use it**
1. Add the indicator to a chart (for example NIFTY or BANKNIFTY, 5-minute).
2. Open Settings > Inputs and paste your levels into the **Levels** box, one per line:
   `Spot=22753`, `Max pain=22750`, `Gamma flip=22749`, `OI call wall=23000`, `OI put wall=22500`, `GEX call wall=23000`, `GEX put wall=22600`, `POC=22707.5`, `VAH=22720`, `VAL=22590`.
   Any other name also works and is drawn in grey.
3. Optional lines: `~ text` adds a regime description line (the first `~` line is the box title), and `# text` is a comment. A first line such as `# ... close 2026-10-26` tells the script which end-of-day the numbers come from.
4. Choose which lines to show with the checkboxes in "Show / hide lines".

**Where do the levels come from?** The text can be copied from the free BharatPulse dashboard (Options tab > "Levels for TradingView" > Copy levels). Find BharatPulse on GitHub (user abhijitkikale) to get it. You can also type your own levels in the same format.

**End-of-day levels are for the next session.** The date in the first line is read, and lines and setups are drawn only on the next trading day (numbers from the 26th close are used on the 27th). Before that day starts, the lines are shown going forward from the last bar. This can be switched off in the settings.

**Settings**
Line width, extend to the left, label distance and minimum gap, label size, which levels to show, touch tolerance (% of the level), what counts as a doji (body as a % of the candle range), bars allowed between steps, bars price must leave the level before the retest, strike step (Auto / 50 / 100), "show signals from" date, and on/off switches for markers, the setups table and the regime box.

**Notes**
- The script does not fetch data; it draws only the numbers you paste, so it works on any symbol and timeframe. Levels are only meaningful for the instrument they were calculated for.
- Signals use closed candles only, so they do not repaint intrabar.
- The touch > doji > retest rule is a mechanical pattern filter. It has not been backtested here and past behaviour does not guarantee future results.
- For information and education only. It is not investment advice and not a recommendation to buy or sell any security or option.

## Tags
levels, support resistance, options, gamma, nifty, banknifty, volume profile, retest

## Before you publish: TradingView house rules
- Script descriptions should be self-contained and in English. Links to outside websites in the description can be removed by moderators, so the description names BharatPulse on GitHub as plain text with no hyperlink.
- Do not claim profits, accuracy or "high win rate". The wording above avoids that.
- Choose **Open-source** (easiest to get approved) or Protected, and a **Public** publication. Add a clean chart screenshot that shows the lines, the top-left table and the top-right box, with no other indicators.
