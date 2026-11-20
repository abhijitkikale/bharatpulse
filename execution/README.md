# FYERS execution engine (sub-project of the BharatPulse dashboard)

The dashboard is the brain. This sub-project only carries out what the dashboard decides and reports what FYERS actually did. **It changes no dashboard file**: it reads `data/stream_state.json` (the dashboard's live state) and writes only inside `execution/`. PAPER is the default mode; LIVE is armed by you, by typing a phrase, in the running process, and is never restored after a restart.

> No real order was placed while this was built or tested. Everything below was exercised against a scripted mock of FYERS. The first live order is yours to watch (section 10).

## 1. The existing architecture (what was found, nothing was changed)
`tools/stream_server.py` (Python, started by the helper) pulls Nifty 1-minute candles and the option chain from Fyers, finds CALL/PUT setups (`pipeline/stream_setups.py`, from TradingView's alert log when available), and runs the paper-trade engine `pipeline/stream_trades.py::run_trades` every cycle. The result is the **trade object** list, written to `data/stream_state.json` (`["trades"]["trades"]`), shown on the live page, saved to `data/journal/`, sent to Telegram (`pipeline/stream_alerts.py`). Fyers authentication for data is the helper's daily sign-in; orders use a separate Algo Trading app token (`tools/fyers_order_login.py` -> `data/fyers_order_token.json`). The earlier, simpler order router (`pipeline/order_manager.py`, `tools/order_router.py`, default off) is left exactly as it was; this engine refuses LIVE while that router is set to `live`.

## 2. One paper trade, signal to P&L (a real one from 19 Nov)
Setup 09:37 CALL 25200 "Now VWAP 25177.06 (break)" -> the option must trade above the setup candle's high (trigger 135.85) within 5 minutes -> **entry 09:38 at 135.85**, **target 190.19 (+40%)**, **stop 122.27 (-10%)** -> the dashboard raises the stop as the premium runs: 09:46 to 142.64, 09:50 to 156.23, 09:51 to 176.60 -> **exit 09:52 at 176.60 (TRAIL), +30%** -> journal and Telegram updated. All of these numbers are fields of the trade object (`entry`, `target`, `stop`, `stop_now`, `status`, `exit`, `shifts`).

## 3. Files
New (all under `execution/`): `config.py` (paths, execution settings), `models.py` (trade object -> `Intent`, tags, ticks), `store.py` (SQLite records), `brokers.py` (`FyersBroker`, `PaperBroker`), `instrument.py` (instrument-master validation), `engine.py` (the state machine), `order_feed.py` (order WebSocket), `trade_source.py` (reads the dashboard), `mode.py` (PAPER/LIVE, arming, KILL), `run.py` (runner), `ui_server.py` (status page, port 8780), `__main__.py` (CLI), `mock_broker.py`, `tests/` (40 tests), `.gitignore`, this README.
Modified: **none**.

## 4. Live execution architecture
```
Dashboard (stream_server + stream_trades)  ->  data/stream_state.json  ->  trade_source (read-only)
        -> Intent (the dashboard's trade object, nothing recomputed)
        -> Mode selector:  PAPER (default)  ->  PaperBroker  -> PAPER records
                           LIVE  (armed)    ->  FyersBroker  -> FYERS REST /orders/sync ...
        FYERS order WebSocket -> engine (fills, cancels)      REST orderbook / tradebook / positions every 10 s -> reconciliation
        -> execution.db (SQLite) + logs/*.jsonl + Telegram (LIVE messages and every warning) + the status page
```
One process (`python -m execution run`), no webhook server, no new database engine (SQLite is in the standard library).

## 5. FYERS API used (v3, base `https://api-t1.fyers.in/api/v3`, header `Authorization: app_id:access_token`)
Checked against FYERS' published reference (github.com/FyersDev/fyers-skills `references/endpoints.md`, `orders.md`, `websocket.md`, the sample code repository) and the installed official SDK `fyers-apiv3` 3.1.11 (its endpoint table, `place_order` docstring and WebSocket field map). The SDK is used for the order WebSocket; REST is called directly with `requests` (same endpoints as the SDK, no extra dependency).
| Purpose | Call |
|---|---|
| place / modify / cancel | `POST` / `PATCH` / `DELETE /orders/sync` (SDK `place_order`, `modify_order`, `cancel_order`) |
| orderbook, by id or tag | `GET /orders` (`?id=` / `?order_tag=1:<tag>`) |
| tradebook, positions, funds, profile | `GET /tradebook`, `/positions`, `/funds`, `/profile` |
| order updates | `wss://socket.fyers.in/trade/v3` through `fyers_apiv3.FyersWebsocket.order_ws.FyersOrderSocket` (`OnOrders,OnTrades,OnPositions`) |
| instrument master | `https://public.fyers.in/sym_details/NSE_FO.csv` |
Order types 1 limit / 4 stop-limit; side 1 buy / -1 sell; productType `INTRADAY`; validity `DAY`; `orderTag` 1 to 30 alphanumeric characters. Success codes 1101 placed, 1102 modified, 1103 cancelled; **201 means sent but not acknowledged**. At most 10 order operations per second (the engine keeps under 8).
Not used on purpose: GTT / OCO (FYERS supports only CNC, MARGIN and MTF there, not INTRADAY) and bracket / cover orders (not for these options).

## 6. How the dashboard's values reach FYERS
| Dashboard trade object | At FYERS |
|---|---|
| `symbol`, `side` CALL/PUT | the contract is the `symbol` (CE for a CALL, PE for a PUT; checked against the instrument master); the dashboard always BUYs the option |
| `entry` | BUY **limit = entry + `entry_cushion_pct`** (2%, so it is marketable; an execution setting, not strategy) |
| quantity | `lots` (1, capped by `max_lots`) x the **lot size from the instrument master** (the dashboard's paper trades carry no quantity); cross-checked, never invented |
| `stop_now` (initial stop, then every trailing / regime move) | a real **stop-loss order** (SL-L): trigger = `stop_now` exactly (absolute, **not rescaled to the fill price**), limit = trigger less `sl_limit_gap_pct` (1.5%, SL-L needs a limit); every rise of `stop_now` modifies that order; a lower value is never applied |
| `target`, `status` TARGET | the target is **managed**: when the dashboard says TARGET the stop order is cancelled and confirmed, then the position is sold (limit `exit` less `exit_cushion_pct`). No second resting sell order, because it could reverse the position |
| `status` STOP / TRAIL / DAY END | the broker's stop normally fills first; if the dashboard says over and the stop has not filled, the same cancel-then-sell exit runs |
Missing `symbol`, `side`, `entry`, `stop_now`, `target`, `status` or `entry_time` = the trade is not executed and the problem is told. Nothing in the engine is a strategy number; the settings in `config.py` are order-handling and account-safety limits (the owner's earlier order-router specification), editable in `execution/config.json`.

## 7. Order status and partial fills
Phases per signal: `ENTRY_PENDING_SEND` -> `ENTRY_SENT` | `ENTRY_UNCERTAIN` -> `PROTECTED` -> `EXIT_SENT` -> `CLOSED`; without a position `SKIPPED` / `REJECTED` / `MISSED`; needing a human `STUCK` / `CHECK_MANUALLY`.
* Entry not filled in `entry_wait_s` (45) is cancelled, the cancel is confirmed, and it is **not retried**.
* Partial fill: after the wait the rest is cancelled (confirmed) and the stop-loss order is placed for **exactly the filled quantity**; if the broker's position differs later, the stop order is resized.
* Live P&L uses the **broker's** average entry and exit prices, never the dashboard's.
* A stop order that cannot be placed (rejected, unanswered) means an **immediate exit**; the position is never left unprotected. A stop order cancelled or rejected later gets the same treatment.
* An exit that does not fill is re-priced up to `exit_tries` (3) times, then marked STUCK with a loud message.
* Order updates arrive by WebSocket when it is connected; the REST orderbook confirms every important step and carries on alone if the socket is down (shown on the page).

## 8. Duplicate-order prevention and reconciliation
* Every dashboard trade gets a stable **signal id** (day, contract, setup time, entry time, entry price; not the trade number, which the dashboard may renumber). The SQLite row `(signal id, mode)` is **claimed before any order is sent**; a second pass, a reconnection or a restart finds the claim and sends nothing.
* **Lost answers:** a timeout, HTTP 5xx / 429, a 201 or an unreadable reply is "uncertain". The order is then looked up in the orderbook by its tag (`BP<12 hex>E`/`S`/`X`); it is **never resent**. Not found after `uncertain_wait_s` (30): `CHECK_MANUALLY`, orders halt for the day, Telegram says so.
* One position at a time; executed-entries cap per day; daily loss limit (from broker-confirmed prices); no order for a trade older than 3 minutes, before `first_order` or from `square_off` (15:15 IST: every live position is exited).
* The stop order is cancelled **and confirmed** before an exit is sent (never two sells); a cancel that cannot be confirmed sends no exit and raises an alert.
* **Reconciliation** every 10 s and after every exit: the broker's positions are the truth. A position closed by hand is noticed and recorded; a quantity mismatch resizes the stop; a position the engine did not open is reported, **not touched**, and halts new orders. A zero reading right after a fill is not believed for 20 s (position feeds lag).
* A stale dashboard (state file older than 90 s or from another day): no new entries, and an open position is held at its current stop until the dashboard is back (the broker stop protects it).

## 9. Test results
`python -m pytest -q execution/tests` : **40 passed** (mock FYERS; no network). The dashboard's own suite `python -m pytest -q tests`: **161 passed**, unchanged (Stage 1, regression).
Stage 2 scenarios covered: successful order; rejected order; partial fill; timeout with a lost acknowledgement (found by tag, never resent); timeout where the order never arrived; duplicate signal and restart (also while protected); cancellation of an unfilled entry; stop-loss execution; target execution (cancel confirmed before the sell); trailing stop moves (never lowered); stop placement failure; unconfirmed cancel; WebSocket push and a down WebSocket; position closed by hand; quantity mismatch; unmanaged position; unreadable orderbook; trades already over or too old; stale dashboard; limits; emergency exit; PAPER mirroring the dashboard exactly; LIVE and PAPER records kept apart; the FYERS adapter's payloads, answer classification, status codes and tags; the status page controls (header, typed phrases, local host only); LIVE refused without the phrase, a token, with the old router live or with the KILL file; a restart is PAPER.
Stage 3 (live connectivity, read-only) was **not run here**: it needs your Algo Trading app token. It is one command: `python -m execution check`.

## 10. Turning LIVE on, by hand (Stage 4)
1. **Once:** activate the Algo Trading app at myapi.fyers.in (registered static IP = the address this PC reaches Fyers from; redirect URL `http://127.0.0.1:5051/`); set `FYERS_ORDER_APP_ID` and `FYERS_ORDER_APP_SECRET` (see `tools/fyers_order_login.py`).
2. **Each morning after 06:30 IST:** `python tools/fyers_order_login.py` (you type your PIN / TOTP at Fyers; token goes to `data/fyers_order_token.json`, never to GitHub).
3. `python -m execution check` -> every line must say PASS (profile, funds, orderbook, tradebook, positions, WebSocket, instrument master). Fix anything that says FAIL first.
4. Keep `data/orders_config.json` at `"mode": "off"` (the engine refuses LIVE otherwise: two routers would double every order).
5. `python -m execution run`, open http://127.0.0.1:8780/ . It starts in **PAPER**: run it like that for several sessions and compare its paper records (`python -m execution status`) with the dashboard's journal.
6. To go live, during market hours with the Fyers app open on your phone: click **Activate LIVE...** and type `ACTIVATE LIVE`. A red LIVE banner stays on while it is on. One lot per trade (`lots` in `execution/config.json`, hard-capped by `max_lots`).
7. **Watch the first trades.** FyersBroker follows the documentation and the SDK but has never met the real service; confirm in the Fyers app that the entry, the stop-loss order (trigger and limit) and every stop move are what the page says.
8. **Stop:** `Switch LIVE off` (no new orders; an open position keeps its stop and is still managed) or **EMERGENCY EXIT** (type `EXIT NOW`: cancels unfilled entries, exits the position, writes `execution/state/KILL`; `python -m execution unkill` removes it). Closing the engine does not remove a stop-loss order already at the broker: check the Fyers app.

## 11. On the dashboard's refresh page (added 20 Nov, at the owner's request)
`Refresh BharatPulse.bat` now has an **Order execution (FYERS)** card: whether the engine runs and in which mode (PAPER, or a red LIVE line), the order-app token (valid until 06:30 IST), whether the order-app details are set on this PC, and three buttons: **Sign in to the order app** (runs `tools/fyers_order_login.py`; you type your PIN / code on Fyers' page), **Start execution engine (PAPER)** and **Open the execution page**. The engine also starts by itself, **in PAPER**, with the live updates. **LIVE is never switched on from the helper page**: that stays on the execution page (typed phrase). Code: `execution/helper_card.py` plus a few lines in `tools/refresh_app.py` (the card, two guarded POST routes, one call in `start_live`). Environment variables set after the helper started are found without restarting it. A normal morning is now: sign in to Fyers (data) as usual, press **Sign in to the order app**, check the card says PAPER and the token is valid.

## What I could not verify (say so when you test)
The exact orderbook / tradebook / position field names beyond the SDK's own field map; whether `?order_tag=1:<tag>` filters as documented (the engine falls back to scanning the whole orderbook); how FYERS answers an SL-L order whose trigger is already through the market (the engine treats a rejection as "exit now"); and the IP / token error wording (the engine treats HTTP 401/403, "token", "authenticat" and "IP" as an authentication problem and halts). Stage 3 and the first small live trades are where these get confirmed.
