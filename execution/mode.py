"""Execution mode: PAPER (default) or LIVE.

LIVE exists only in the memory of the running process. Starting the engine always means PAPER, so a restart can never put you back into LIVE by itself. Arming LIVE needs
all of:  the exact phrase typed by you  +  a valid trading-app token for today  +  the dashboard's own order router NOT in "live" (two routers would double every order)
+  no KILL file. `disarm` stops new entries at once (an open position keeps being managed and stays protected); the KILL file is the hard stop: while it exists LIVE
cannot be armed."""
import datetime as dt
import json

from . import config

PHRASE = "ACTIVATE LIVE"
KILL = config.STATE / "KILL"


def load_token(now=None):
    """(app_id, access_token) from the trading app's token file if it is still valid, else None. The token comes from tools/fyers_order_login.py (daily sign-in, 2FA by you)."""
    try:
        d = json.loads(config.ORDER_TOKEN.read_text(encoding="utf-8"))
        exp = dt.datetime.fromisoformat(d["expires"])
        if (now or dt.datetime.now(exp.tzinfo)) < exp:
            return d["app_id"], d["access_token"]
    except Exception:
        pass
    return None


def old_router_live():
    try:
        return json.loads(config.OLD_ROUTER_CONFIG.read_text(encoding="utf-8")).get("mode") == "live"
    except Exception:
        return False


class ModeManager:
    def __init__(self):
        self.mode = "PAPER"
        self.since = None
        self.note = "PAPER (default)"

    @property
    def live(self):
        return self.mode == "LIVE"

    def checks(self, now=None):
        """[(ok, text)] for everything LIVE needs, for the status page and the arm decision."""
        return [(load_token(now) is not None, "a valid trading-app token for today (run tools/fyers_order_login.py)"),
                (not old_router_live(), "the dashboard's own order router is not set to live (data/orders_config.json)"),
                (not KILL.exists(), "no KILL file (execution/state/KILL)")]

    def arm(self, phrase, now=None):
        if phrase != PHRASE:
            return False, f"to activate LIVE type exactly: {PHRASE}"
        bad = [t for ok, t in self.checks(now) if not ok]
        if bad:
            return False, "LIVE not activated, missing: " + "; ".join(bad)
        self.mode, self.since, self.note = "LIVE", (now or dt.datetime.now()).isoformat(timespec="seconds"), "LIVE (armed by you in this session)"
        return True, "LIVE is ON for this session. A restart returns to PAPER."

    def disarm(self, why="disabled"):
        self.mode, self.note = "PAPER", f"PAPER ({why})"
        return True, f"back to PAPER: {why}"

    def hard_kill(self, why="emergency"):
        KILL.parent.mkdir(parents=True, exist_ok=True)
        KILL.write_text(f"{dt.datetime.now().isoformat()} {why}\n", encoding="utf-8")
        return self.disarm(why)
