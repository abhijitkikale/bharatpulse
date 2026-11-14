# Nodes agent (Heatseeker / Atlas ideas for Nifty): design

Date: 2026-11-14. Status: approved in conversation (approach, phase order, "grades are information only first"); built in phases.

## Intent and why
The owner trades Nifty options by watching how price reacts at levels. Skylit's Heatseeker (a strike x expiry grid of dealer exposure, with "nodes") and Atlas (nodes on the chart, with flow, VWAP and expected-move cones) describe what makes a level more or less likely to produce a reaction. Skylit calls this "context, not a trigger"; for the owner it means **probability**: the node says where, the existing reaction rule (touch, doji, retest, close beyond) says when. The agent turns the option chain into nodes, shows them on the live page without any clicking (the owner is in the office), and gives every setup a grade with the reasons in plain words. Skylit publishes no thresholds and calls its rules untested teaching, so the system also has to measure its own odds on Nifty.

## Goals
1. A separate program (the **nodes agent**) that every minute in market hours builds a Nifty node snapshot from the Fyers option chain and writes `data/nodes/latest.json`.
2. Live page, hands-free: orbs on the chart (size = strength, yellow positive, purple negative, King starred), a compact heatmap (strikes x next expiries, auto-centred), and an A/B/C grade on each setup chip.
3. Velocity (building / melting) from an in-memory buffer; the same buffer is saved to `data/nodes/YYYY-MM-DD.jsonl` (the recorder, kept for the end-of-day page, replay and calibrating the grades later).
4. Plugging in or removing the agent changes nothing else: the page works without `/nodes.json`.

## Non-goals (this version)
- Grades do not change paper trades. After some days of watching, useful ones become rules (as the 09:25, regime and afternoon rules did).
- No vanna (VEX), no Bank Nifty / Sensex / Trinity, no expected-move cone drawing, no flow bars, no Telegram messages, no end-of-day replay page. These are Phase 2 and 3.

## Anti-goals
- Never a second source of trades or a signal sender. Never slows or breaks the existing stream page, helper or Telegram.
- No controls the owner has to touch during the day.

## Constraints
- Data: the Fyers option chain only (per strike: last price, open interest, previous open interest, volume, symbol), index candles from Fyers, the existing end-of-day files. No tick-level trades, no dealer/customer split, no dark pool in India.
- Reuse `pipeline/gex.py` (Black-76, implied volatility from prices, forward from put-call parity, calls positive and puts negative) so numbers agree with the existing live levels.
- Windows PC; one Fyers token (`data/fyers_token.json`) shared with the helper; Fyers rate limits (about 4 chain calls a minute are used here).
- Weekly Nifty expiry is on Tuesday (the 17 Nov chain); expiry-day gamma dominates.

## Approaches considered
1. **Separate agent writing a file the page reads (chosen).** `tools/nodes_agent.py` + pure functions in `pipeline/nodes.py`; the stream server only serves `/nodes.json`. Clean to add or switch off; matches the owner's request for a different agent.
2. Inside the stream server: quickest wiring, but mixes new work into live code, and a failure could stall the page.
3. TradingView only (pasted text): smallest, but no grid and no grades on the page.

## Architecture
```
Fyers chain (first 4 expiries) + index candles (today) + data/stream_state.json (levels, setups)
  -> exposure grid per expiry        (gex.compute_gex, one call per expiry)
  -> node classifier (Front column)  King, gatekeeper, floor, ceiling, pika/barney, air pockets, midpoint, decoy flag
  -> dynamics                        touches, fresh/delivered (from candles), velocity (from the buffer)
  -> context scorer                  A/B/C + reasons per setup (kept once computed)
  -> data/nodes/latest.json  (+ data/nodes/YYYY-MM-DD.jsonl buffer)
stream_server  GET /nodes.json  ->  stream page: orbs, heatmap panel, grade badges (node alerts in Phase 2)
```
Started by the helper next to the TradingView paster (never twice: a lock file); also runnable by hand (`--once`).

## Definitions (ours; Skylit's are unpublished, all thresholds are first guesses to calibrate later)
- **Value of a strike** = call GEX + put GEX of that expiry (calls +, puts -), in rupee crore per 1% move, from `compute_gex`.
- **Columns**: the nearest 4 expiries. **Ranking column ("Front")** = the nearest expiry, as the existing walls.
- **Strength %** = |value| / |King value| of the column. **Significant** = at least 25%.
- **King** = largest |value| in the column. **Gatekeeper** = the largest significant node strictly between spot and the King. **Floor / ceiling** = the nearest significant node below / above spot (the edges of the range). **Pika / barney** = up to two largest significant positive / negative nodes. **Air pocket** = 3 or more consecutive strikes all under 10% of the King. **Midpoint** = spot between floor and ceiling at 35%-65% of a range of at least 100 points. **Decoy flag** on the King when its distance from spot exceeds 1.5 x the expected move (0.8 x the Front at-the-money straddle).
- **Touches** = number of times today's 1-minute candles entered the strike +/- 5 points. **Fresh** = 0 touches, else **delivered**.
- **Velocity** over 5 and 15 minutes = (|value now| - |value then|) / |King now| x 100, in percentage points: positive building, negative melting.
- **Regime** = price against the live gamma flip of the Front column, same rule as the page's regime box.

## Setup grade (information only)
Points, per setup (CALL or PUT) at its level L:
- backing node on the protected side within 30 points: strength >= 60% +2, >= 25% +1;
- room to the next significant node on the target side: >= 50 points +1, < 25 points -1;
- backing node fresh +1, or touched 3 or more times -1;
- confluence, two or more other levels within 10 points of L: +1;
- regime: negative gamma and with the move +1, against the move -2;
- no significant node within 40 points either side of L: -1.

Grade A >= 3, B 1-2, C <= 0. The first grade computed for a setup is kept; it is marked "late" if the snapshot is more than 3 minutes newer than the setup.

## Live page (hands-free)
Orbs drawn on an overlay at the right of the chart for the Front column's significant nodes (max 7), label "strike, strength %, role, arrow for velocity". A narrow heatmap panel (15 strikes around spot x 4 expiries, King starred, spot row marked) at the top right of the chart. Grade badge on each setup chip. `?nodes=0` hides the layer. All automatic.

## Phase 2 and 3 (not built now)
Phase 2: rules in the trade logic (skip midpoint, shrink target behind a gatekeeper, trail wider through air pockets), expected-move cone, node approach/tap alerts through the existing pop-up.
Phase 3 (end-of-day page): node replay from the saved buffer, a reaction ledger (every approach to a node: held / passed / overshot, with its features) to replace the guessed weights with measured ones, and a test of the dealer-sign assumption (static convention versus one inferred from open-interest change).

## Risks and open points
- The dealer-sign convention (dealers long calls, short puts) may fit India poorly; Phase 3 tests it.
- Fyers rows may carry bid/ask and open-interest change (not stored today): to verify on a live session.
- Open interest refreshes only every few minutes, so minute-to-minute node changes are mostly price and IV effects.
- Expiry day: the Front column is 0DTE; the model runs with one day left, as the live levels already do.
- The agent reads `data/stream_state.json` for levels and setups; if it is missing or stale, grades are skipped, nodes still publish.

## Calls made (not asked)
Front column ranking, 4 expiries, the 25% / 10% / 30-point thresholds and grade weights, a 60-second cycle, file-based hand-off, and saving the buffer from day one.
