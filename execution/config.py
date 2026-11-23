"""Paths and settings of the execution engine.

STRATEGY parameters (symbol, side, entry, stop, target, trailing, number of trades, when to trade) are NEVER set here: they come from the dashboard's trade object.
What is configured here is only how the orders are sent and the account-level safety limits. The defaults below are the owner's earlier order-router specification
(one lot, no chasing, limit orders with a small cushion so they are marketable, loss limit, executed-order cap, old signals ignored); change them in
execution/config.json (git-ignored), never in code."""
import json
import pathlib

EXEC = pathlib.Path(__file__).resolve().parent
ROOT = EXEC.parent                                           # the dashboard folder
DATA = EXEC / "data"
LOGS = EXEC / "logs"
STATE = EXEC / "state"
CONFIG_FILE = EXEC / "config.json"

DASH_STATE = ROOT / "data" / "stream_state.json"             # the dashboard's live state, written by tools/stream_server.py (read-only here)
ORDER_TOKEN = ROOT / "data" / "fyers_order_token.json"       # written by tools/fyers_order_login.py (the trading app's token, never sent to GitHub)
OLD_ROUTER_CONFIG = ROOT / "data" / "orders_config.json"     # the dashboard's own order router: LIVE here is refused while that one is "live"
PAUSE_FILE = ROOT / "data" / "trading_pause.json"            # Telegram /pause of the dashboard (new entries only)
JOURNAL_SETTINGS = ROOT / "data" / "journal_settings.json"   # {"lot": 65, ...}: only cross-checked against the instrument master

DEFAULTS = {
    "lots": 1,                       # number of lots per trade (the dashboard's paper trades are percentages, they carry no quantity)
    "max_lots": 1,                   # hard cap whatever "lots" says
    "product_type": "INTRADAY",
    "validity": "DAY",
    "entry_cushion_pct": 2.0,        # the BUY is a limit this much above the dashboard's entry price so that it is marketable (execution detail, not strategy)
    "sl_limit_gap_pct": 1.5,         # the stop-loss order is stop-limit: its limit sits this much under the dashboard's stop (SL-L needs a limit price)
    "exit_cushion_pct": 2.0,         # an exit SELL is a limit this much under the reference price (marketable)
    "entry_wait_s": 45,              # an entry not filled in this time is cancelled and not retried
    "uncertain_wait_s": 30,          # how long an unacknowledged order is looked up in the orderbook before it is declared unconfirmed
    "exit_tries": 3,
    "max_signal_age_min": 3,         # nothing is ordered for a dashboard trade older than this
    "max_entries_per_day": 5,        # EXECUTED entries
    "max_daily_loss_inr": 5000,
    "square_off": "15:15",           # IST: every live position is exited at this time
    "first_order": "09:16",          # IST: no order before this
    "reconcile_every_s": 10,
    "cycle_s": 3,
    "stale_state_s": 90,             # the dashboard state older than this is not trusted for new entries
    "ws_enabled": True,
    "ui_port": 8780,
    "tick": 0.05,
    "stop_mode": "dashboard",        # "dashboard": the stop order uses the dashboard's absolute stop; nothing is rescaled to the fill price
    "max_ops_per_s": 8,              # FYERS allows 10 order operations a second in total
    "executor": "api",               # "api": orders go straight to the FYERS API (needs the registered static IP). "automate": through FYERS Automate webhooks (automate.py). Revert = "api".
    "automate_entry_watch_s": 120,   # Automate: how long to wait for the position to appear after the entry webhook
    "automate_exit_wait_s": 45,      # Automate: how long to wait for the position to close after the exit webhook before sending it once more
}
INT_KEYS = {"lots", "max_lots", "entry_wait_s", "uncertain_wait_s", "exit_tries", "max_signal_age_min", "max_entries_per_day", "reconcile_every_s", "cycle_s", "stale_state_s", "ui_port", "max_ops_per_s", "automate_entry_watch_s", "automate_exit_wait_s"}


def load(path=None):
    cfg = dict(DEFAULTS)
    try:
        raw = json.loads(pathlib.Path(path or CONFIG_FILE).read_text(encoding="utf-8"))
        for k, v in raw.items():
            if k in DEFAULTS:
                cfg[k] = int(v) if k in INT_KEYS else v
    except (OSError, ValueError):
        pass
    cfg["lots"] = max(1, min(int(cfg["lots"]), int(cfg["max_lots"])))
    if cfg["executor"] not in ("api", "automate"):
        cfg["executor"] = "api"                                  # anything unknown means the original, direct-API path
    return cfg
