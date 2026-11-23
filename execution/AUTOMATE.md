# FYERS Automate executor (alternative to the direct-API engine)

**Why.** FYERS accepts API orders only from one registered static IP; a Qatar home line has none (the VPN's address is blocked by FYERS). Orders placed by
**FYERS Automate** run on FYERS' servers and need no IP. The dashboard stays the brain; Automate only places and closes the order.

**Safe by design**
* The direct-API engine (`engine.py`, `FyersBroker`) and the PAPER engine are **unchanged**. `"executor": "api"` is the default.
* Revert at any time: `python -m execution executor api`, then restart the engine. (A restart is always PAPER.)
* LIVE still needs the typed phrase `ACTIVATE LIVE` (page http://127.0.0.1:8780/ or Telegram `/exec_live` + `/confirm`). Telegram `/exec_exit`, `/exec_off`,
  `/exec_status` work for both executors.
* Webhook addresses are secrets: they live only in `execution/automate.json` (git-ignored), only `https://*.fyers.in/` hosts are accepted, they are never printed or logged.

## How a trade runs
1. Dashboard trade appears (OPEN) -> the engine sends the slot's **entry webhook** (once, never repeated).
2. The Automate workflow buys the ATM CE/PE (1 lot, intraday) with a wide **emergency stop** (the safety net if this PC or the internet dies).
3. The engine watches the broker's **positions** (read-only API; no static IP needed): a new long NIFTY option of the right side = filled. The strike Automate actually got is recorded.
4. The dashboard's stop / stepped trailing / target decide the exit. When the dashboard says STOP / TRAIL / TARGET / DAY END (or 15:15, or an emergency exit) the engine sends
   the **exit webhook**; the workflow squares the position off. The engine confirms the position is flat and records the broker's realised profit.
5. Problems (no position after 120 s, exit not completing, unknown position, refused webhook) halt new entries for the day, send Telegram and say what to do.

One workflow = one trade a day (a workflow continues once per run). `automate.json` lists the workflows ("slots"); an entry uses the first unused slot of its side.

## 1. The notification-only TEST workflow (no order)
In Automate -> Create automation (manual):
`Start (Only manually)` -> `Webhook trigger` (type **Simple**) -> `Mobile app notification` ("ENTRY received") -> `Webhook trigger` (**Simple**) -> `Mobile app notification` ("EXIT received").
Click **Start**, copy the two webhook addresses into `execution/automate.json`:
```json
{ "test": { "entry_url": "https://api-t2.fyers.in/webhook/execute?id=...", "exit_url": "https://api-t2.fyers.in/webhook/execute?id=..." }, "slots": [] }
```
Then: `python -m execution automate-test --which entry --calls 1` (phone notification arrives), then `--which exit`. Repeat entry a second time to see whether it fires twice.

## 2. A live slot workflow (build one per trade you want per day, CE and PE)
`Start (Daily, 09:10)` -> `Webhook trigger` (Simple) **entry** -> `Place options order with SL & Tgt` -> `Webhook trigger` (Simple) **exit** -> `Square off` (this automation's positions) -> `Stop automation`.
Order node: CE (or PE), **Buy**, product **Intraday** (the default shows "Overnight": change it), strike type **ATM spot** (custom multiple 50), **1 lot**, **current expiry**,
Stop-loss points = the emergency stop (about 15-20 % of a typical premium), leave Target off or very wide, tick "Square off leg with stop-loss or target".
Connect the order node's red (stop hit) output to a notification + `Stop automation`.
```json
{ "slots": [ { "name": "CE1", "side": "CALL", "entry_url": "https://...", "exit_url": "https://..." }, { "name": "PE1", "side": "PUT", "entry_url": "...", "exit_url": "..." } ] }
```
Check: `python -m execution automate-check` (validates, prints no address).

## 3. Switch on / off
```
python -m execution executor automate     # use Automate (restart the engine; activate LIVE as usual)
python -m execution executor api          # back to the direct API
python -m execution unhalt                # clears today's halt for both executors
```

## Not verified yet (the first 1-lot test must show them)
* whether a started workflow can be triggered again the same day, and how quickly a webhook becomes an order (use the TEST workflow, then a 1-lot live test);
* whether Automate sends the entry as a market or a limit order, and what fill it gets; whether "Square off" closes only this automation's position;
* whether this PC can reach `api-t2.fyers.in` (Cloudflare) - the test command shows it.
