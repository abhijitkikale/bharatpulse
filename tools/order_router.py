"""Order router: runs pipeline/order_manager.py against the stream page's trades every few seconds in market hours. OFF BY DEFAULT.

The mode lives in data/orders_config.json ("off" | "dry_run" | "live"); see pipeline/order_manager.py for what each does and for the safety limits. The helper starts this
program with the live updates; it does nothing while the mode is "off". In "live" it needs a valid data/fyers_order_token.json (tools/fyers_order_login.py) and the
Algo Trading app's registered static IP. Every decision is told to your private Telegram chat; steps are logged in data/orders/<day>.log.
  python tools/order_router.py            run until closed
  python tools/order_router.py --once     one pass now (uses the mode in the file), print the messages, exit"""
import argparse
import json
import os
import pathlib
import sys
import time

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import nse_calendar  # noqa: E402
import order_manager as om  # noqa: E402
import trading_pause  # noqa: E402

STATE = ROOT / "data" / "stream_state.json"
TOKEN = ROOT / "data" / "fyers_order_token.json"
LOCK = ROOT / "data" / "orders" / "router.lock"
CYCLE = 5


def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def lock_or_exit():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "w")
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another order router is already running")
        sys.exit(0)
    return f


def page_trades(day):
    """The page's trades for today, or None when the page state is missing, stale (older than 90 s) or from another day."""
    try:
        if time.time() - STATE.stat().st_mtime > 90:
            return None
        s = json.loads(STATE.read_text(encoding="utf-8"))
        if s.get("date") != day:
            return None
        return (s.get("trades") or {}).get("trades") or []
    except Exception:
        return None


def order_token(now):
    """(app_id, token) for live mode, or None when there is no valid one today."""
    try:
        d = json.loads(TOKEN.read_text(encoding="utf-8"))
        if now < pd.Timestamp(d["expires"]):
            return d["app_id"], d["access_token"]
    except Exception:
        pass
    return None


def tell(text):
    try:
        import telegram_notify
        telegram_notify.send(text)
    except Exception:
        pass
    print(now_ist().strftime("%H:%M:%S"), text, flush=True)


def log(day, text):
    try:
        om.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(om.STATE_DIR / f"{day}.log", "a", encoding="utf-8") as f:
            f.write(now_ist().strftime("%H:%M:%S ") + text + "\n")
    except OSError:
        pass


class Router:
    def __init__(self):
        self.dry = om.DryBroker()
        self.warned = set()

    def once(self, t=None, force_session=False):
        t = t or now_ist()
        cfg = om.load_config()
        mode = cfg["mode"]
        if mode == "off" or not (force_session or nse_calendar.in_session(t, lead=0, tail=5)):
            return []
        day = str(t.date())
        trades = page_trades(day)
        if trades is None:
            return []
        if mode == "live":
            tok = order_token(t)
            if not tok:
                if "notoken" not in self.warned:
                    self.warned.add("notoken")
                    tell("[ORDERS] mode is LIVE but there is no valid order token for today: run tools/fyers_order_login.py (or send /orders_off). No order was sent.")
                return []
            broker, label = om.FyersBroker(*tok), "LIVE"
        else:
            broker, label = self.dry, "DRY RUN"
        key = f"{day}-{mode}"
        state = om.load_state(key)
        mgr = om.OrderManager(broker, cfg, state, label=label)
        msgs = mgr.step(trades, t, paused=trading_pause.is_paused_now(t))
        om.save_state(key, mgr.state)
        for m in msgs:
            log(day, m)
            tell(m)
        return msgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    r = Router()
    if a.once:
        print(r.once(force_session=True) or "nothing to do (mode off, no fresh page state, or no new events)")
        return
    lock = lock_or_exit()                                        # noqa: F841
    while True:
        try:
            r.once()
        except Exception as e:                                   # never die: say so and try again
            print(time.strftime("%H:%M:%S"), "order router error:", str(e)[:200], flush=True)
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
