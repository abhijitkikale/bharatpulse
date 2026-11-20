"""The execution engine's card for the dashboard's refresh page (tools/refresh_app.py): the logic lives here, the helper only calls these few functions.

  card()          what the page shows: the order-app token, the order-app details on this PC, whether the engine runs and in which mode
  order_login()   runs tools/fyers_order_login.py (you type the PIN / code on Fyers' own page); the helper never sees them
  start_engine()  starts `python -m execution run` (PAPER). LIVE is NEVER activated from the helper page: it needs the typed phrase on the execution page itself.
Windows environment variables set AFTER the helper started are still found (they are read from the user's registry settings), so you do not have to restart the helper."""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

from . import config

PORT_URL = "http://127.0.0.1:{port}/"
_LOGIN = {"busy": False, "msg": ""}
_ENGINE = {"proc": None}
_CACHE = {"t": 0.0, "v": None}


def user_env(name):
    """The variable from this process, else from the Windows user environment (set after this program started)."""
    v = os.environ.get(name, "").strip()
    if v or sys.platform != "win32":
        return v
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except Exception:
        return ""


def child_env():
    e = dict(os.environ)
    for n in ("FYERS_ORDER_APP_ID", "FYERS_ORDER_APP_SECRET"):
        v = user_env(n)
        if v:
            e[n] = v
    return e


def order_app_ready():
    return bool(user_env("FYERS_ORDER_APP_ID") and user_env("FYERS_ORDER_APP_SECRET"))


def token_card(now=None):
    """{"have", "valid", "text"} for data/fyers_order_token.json (the order app's token, valid to 06:30 IST next day)."""
    try:
        d = json.loads(config.ORDER_TOKEN.read_text(encoding="utf-8"))
        exp = dt.datetime.fromisoformat(d["expires"])
        valid = (now or dt.datetime.now(exp.tzinfo)) < exp
        return {"have": True, "valid": valid, "text": ("valid until " if valid else "expired at ") + exp.strftime("%d %b %H:%M") + " IST"}
    except Exception:
        return {"have": False, "valid": False, "text": "none yet"}


def engine_card(timeout=0.8):
    """Asks the execution page's own status endpoint; running False when nothing answers."""
    try:
        import requests
        r = requests.get(PORT_URL.format(port=config.load()["ui_port"]) + "api/status", timeout=timeout)
        s = r.json()
        live = s.get("mode") == "LIVE"
        return {"running": True, "mode": s.get("mode"), "live": live, "halted": s.get("halted"), "auth": s.get("auth", {}).get("ok"), "ws": s.get("ws", {}).get("connected"),
                "recon": s.get("recon", {}).get("text"), "current": (s.get("current") or {}).get("phase"), "realized": s.get("realized")}
    except Exception:
        return {"running": False, "mode": None, "live": False}


def card(now=None, cache_s=3.0):
    if _CACHE["v"] is not None and time.time() - _CACHE["t"] < cache_s:
        v = dict(_CACHE["v"])
    else:
        v = {"app_ok": order_app_ready(), "token": token_card(now), "engine": engine_card(), "port": config.load()["ui_port"]}
        _CACHE.update(t=time.time(), v=v)
    v["login"] = dict(_LOGIN)
    return v


def order_login(timeout=200):
    """Run the order-app sign-in in the background: it opens the FYERS login in the browser and saves the token. The result line is shown on the page."""
    if _LOGIN["busy"]:
        return "the order-app sign-in is already open: finish it in the browser"
    if not order_app_ready():
        _LOGIN["msg"] = "FYERS_ORDER_APP_ID / FYERS_ORDER_APP_SECRET are not set on this PC (see execution/README.md, section 10)"
        return _LOGIN["msg"]

    def work():
        _LOGIN.update(busy=True, msg="waiting for you to sign in on the FYERS page that opened in the browser (up to 3 minutes)...")
        try:
            r = subprocess.run([sys.executable, str(config.ROOT / "tools" / "fyers_order_login.py")], cwd=str(config.ROOT), env=child_env(), capture_output=True, text=True, timeout=timeout)
            lines = [x for x in (r.stdout + "\n" + r.stderr).splitlines() if x.strip()]
            ok = r.returncode == 0 and any("token saved" in x.lower() for x in lines)
            _LOGIN["msg"] = ("order token saved: " if ok else "order sign-in did not finish: ") + (lines[-1][:140] if lines else "no output")
        except Exception as e:
            _LOGIN["msg"] = f"order sign-in failed: {type(e).__name__}: {str(e)[:100]}"
        finally:
            _LOGIN["busy"] = False
            _CACHE["t"] = 0.0
    threading.Thread(target=work, daemon=True).start()
    return "order-app sign-in started: a FYERS page opens in your browser"


def start_engine():
    """Start `python -m execution run` unless it is already running (a second copy would exit by itself anyway). Always starts in PAPER."""
    if engine_card()["running"]:
        return "the execution engine is already running"
    p = _ENGINE["proc"]
    if p is not None and p.poll() is None:
        return "the execution engine is starting"
    try:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        out = open(config.LOGS / "engine_stdout.txt", "a", encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _ENGINE["proc"] = subprocess.Popen([sys.executable, "-m", "execution", "run"], cwd=str(config.ROOT), env=child_env(), creationflags=flags, stdout=out, stderr=out)
        _CACHE["t"] = 0.0
        return "the execution engine was started in PAPER mode"
    except Exception as e:
        return f"the execution engine could not be started: {type(e).__name__}: {str(e)[:100]}"
