"""Run OBS Studio and the YouTube broadcast from the stream program, through OBS's built-in WebSocket server (OBS 28 and later).

What it does (only when data/obs.json exists and its mode is not "off"; set up with tools/obs_setup.py):
  * starts OBS minimised if it is not running, makes sure the scene "BharatPulse" with the stream page as a Browser source exists;
  * mode "direct": starts the broadcast as soon as the stream page is ready after your Fyers sign-in; mode "at_open": starts it at 09:31 IST
    once the feed is healthy;
  * stops the broadcast at stop_at (default 15:35 IST), or earlier when the market-hours feed has been bad for 10 minutes, and tells you on
    Telegram. After a stop it stays off until the next day. A broadcast you stop yourself is not restarted.
YouTube itself is set up once in OBS (Settings > Stream, your account); the stream starts and ends with OBS's Start / Stop Streaming.
The WebSocket password lives only in data/obs.json on this PC (git-ignored)."""
import base64
import hashlib
import json
import pathlib
import subprocess
import sys
import threading
import time
import uuid

import pandas as pd
try:
    import websocket                 # websocket-client (only needed on the PC that runs OBS)
except ImportError:                  # the cloud build does not have it
    websocket = None

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "data" / "obs.json"
EXES = [r"C:\Program Files\obs-studio\bin\64bit\obs64.exe", r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"]
DEFAULTS = {"host": "127.0.0.1", "port": 4455, "password": "", "mode": "off", "scene": "BharatPulse", "url": "http://127.0.0.1:8766/?theme=dark",
            "stop_at": "15:35", "exe": ""}


class ObsError(Exception):
    pass


class Obs:
    """Minimal OBS WebSocket v5 client."""

    def __init__(self, host="127.0.0.1", port=4455, password="", timeout=10):
        self.host, self.port, self.password, self.timeout, self.ws = host, int(port), password, timeout, None

    def connect(self):
        if websocket is None:
            raise ObsError("the websocket-client package is not installed (pip install websocket-client)")
        self.ws = websocket.create_connection(f"ws://{self.host}:{self.port}", timeout=self.timeout)
        hello = json.loads(self.ws.recv())
        ident = {"rpcVersion": 1, "eventSubscriptions": 0}
        auth = hello["d"].get("authentication")
        if auth:
            secret = base64.b64encode(hashlib.sha256((self.password + auth["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
        self.ws.send(json.dumps({"op": 1, "d": ident}))
        if json.loads(self.ws.recv()).get("op") != 2:
            raise ObsError("OBS did not accept the connection (wrong WebSocket password?)")
        return self

    def call(self, request, data=None):
        rid = uuid.uuid4().hex
        self.ws.send(json.dumps({"op": 6, "d": {"requestType": request, "requestId": rid, "requestData": data or {}}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("op") == 7 and m["d"].get("requestId") == rid:
                st = m["d"]["requestStatus"]
                if not st.get("result"):
                    raise ObsError(f"{request}: {st.get('comment') or st.get('code')}")
                return m["d"].get("responseData") or {}

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def streaming(self):
        return bool(self.call("GetStreamStatus").get("outputActive"))

    def ensure_scene(self, scene, url, configure=True):
        """Create the scene and its Browser source (1920x1080, the stream page) if missing; update the address if it exists.
        configure=True (setup) also makes it the active scene and sets OBS's canvas to 1920x1080 at 30 fps; the runner passes False so it never
        changes what you have selected in OBS."""
        if scene not in [x["sceneName"] for x in self.call("GetSceneList").get("scenes", [])]:
            self.call("CreateScene", {"sceneName": scene})
        name = scene + " page"
        settings = {"url": url, "width": 1920, "height": 1080, "shutdown": False, "restart_when_active": False}
        if name not in [x["inputName"] for x in self.call("GetInputList").get("inputs", [])]:
            self.call("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": "browser_source", "inputSettings": settings, "sceneItemEnabled": True})
        else:
            self.call("SetInputSettings", {"inputName": name, "inputSettings": settings, "overlay": True})
        if not configure:
            return
        self.call("SetCurrentProgramScene", {"sceneName": scene})
        try:
            self.call("SetVideoSettings", {"baseWidth": 1920, "baseHeight": 1080, "outputWidth": 1920, "outputHeight": 1080, "fpsNumerator": 30, "fpsDenominator": 1})
        except ObsError:
            pass                                                    # not allowed while OBS is streaming: the scene is the part that matters


def load_config():
    """The settings from data/obs.json merged over the defaults, or None when the file is missing or unreadable."""
    try:
        return {**DEFAULTS, **json.loads(CFG.read_text(encoding="utf-8"))}
    except Exception:
        return None


def _trading_day(t):
    """Is t's date an NSE session day (holidays and special sessions from the calendar)? Falls back to Monday to Friday."""
    try:
        sys.path.insert(0, str(ROOT / "pipeline"))
        import nse_calendar
        return nse_calendar.is_trading_day(t.date())
    except Exception:
        return t.weekday() < 5


def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def _hm(text):
    h, m = str(text).split(":")
    return int(h), int(m)


def decide(cfg, t, streaming, ready, healthy, done, bad_minutes, trading_day=None):
    """The next action: 'start', 'stop', 'stale-stop' or None.
    ready: the stream page has data; healthy: the market-hours feed is fine; done: a broadcast was already started or stopped today;
    bad_minutes: how long the feed has been bad during market hours."""
    hm, stop_hm = (t.hour, t.minute), _hm(cfg["stop_at"])
    if trading_day is None:
        trading_day = t.weekday() < 5
    if not trading_day:
        return None
    if streaming:
        if hm >= stop_hm:
            return "stop"
        if (9, 15) <= hm < (15, 30) and bad_minutes >= 10:
            return "stale-stop"
        return None
    if done or hm >= stop_hm or not ready or not (5, 0) <= hm:
        return None
    if cfg["mode"] == "direct":
        return "start"
    if cfg["mode"] == "at_open" and hm >= (9, 31) and healthy:
        return "start"
    return None


def obs_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe"], capture_output=True, text=True, timeout=15).stdout
        return "obs64.exe" in out
    except Exception:
        return False


def show_window():
    """Bring OBS's main window to the front; it may be hidden in the system tray. Returns True when a window was shown."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15).stdout
        pids = {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"obs64.exe"')}
    except Exception:
        return False
    user32, found = ctypes.windll.user32, []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value.startswith("OBS "):
                    found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for h in found:
        user32.ShowWindow(h, 9)                                  # SW_RESTORE
        user32.SetForegroundWindow(h)
    return bool(found)


def launch_obs(cfg, visible=False):
    """Start OBS if it is not running (no 'safe mode' prompt), minimised to the tray unless visible=True. Returns True when it was started."""
    if obs_running():
        return False
    exe = next((e for e in ([cfg.get("exe")] if cfg.get("exe") else []) + EXES if pathlib.Path(e).exists()), None)
    if not exe:
        return False
    subprocess.Popen([exe, "--disable-shutdown-check"] + ([] if visible else ["--minimize-to-tray"]), cwd=str(pathlib.Path(exe).parent))
    return True


def _connect(cfg, tries=1, pause=3):
    last = None
    for _ in range(tries):
        try:
            return Obs(cfg["host"], cfg["port"], cfg["password"]).connect()
        except Exception as e:
            last = e
            time.sleep(pause)
    raise ObsError(f"could not reach OBS ({type(last).__name__}); is it open with the WebSocket server on?")


NO_KEY = ("OBS accepted the command but the stream did NOT start. Most likely OBS has no stream key: copy the stream key from YouTube Studio (Stream page) "
          "and paste it in OBS > Settings > Stream (YouTube - RTMPS, 'Use stream key'). Click OK on OBS's error window.")


def started_ok(obs, wait=12):
    """After StartStream: True once OBS reports the output active (it can fail a moment later, e.g. without a stream key)."""
    for _ in range(wait):
        time.sleep(1)
        if obs.streaming():
            return True
    return False


def golive(cfg):
    """Select the BharatPulse scene, reload its page and start the broadcast (used by the Go live button). Returns a short message."""
    if not obs_running():
        launch_obs(cfg)
        time.sleep(20)
    obs = _connect(cfg, tries=15)
    try:
        obs.ensure_scene(cfg["scene"], cfg["url"], configure=False)
        if obs.streaming():
            return "the broadcast is already live"
        obs.call("SetCurrentProgramScene", {"sceneName": cfg["scene"]})
        try:
            obs.call("PressInputPropertiesButton", {"inputName": cfg["scene"] + " page", "propertyName": "refreshnocache"})
        except ObsError:
            pass
        time.sleep(3)
        obs.call("StartStream")
        return "broadcast started (check YouTube Studio)" if started_ok(obs) else "FAILED: " + NO_KEY
    finally:
        obs.close()


def stop_stream(cfg):
    obs = _connect(cfg)
    try:
        if not obs.streaming():
            return "the broadcast was not live"
        obs.call("StopStream")
        return "broadcast stopped"
    finally:
        obs.close()


def snapshot(cfg):
    """{configured, running, live} for the helper page; live is None when OBS cannot be asked."""
    if not cfg or not cfg.get("password"):
        return {"configured": False, "running": obs_running(), "live": None}
    out = {"configured": True, "running": obs_running(), "live": None}
    if out["running"]:
        try:
            obs = Obs(cfg["host"], cfg["port"], cfg["password"], timeout=3).connect()
            try:
                out["live"] = obs.streaming()
            finally:
                obs.close()
        except Exception:
            pass
    return out


def feed_health(state, t):
    """(ready, healthy): ready = the page has data; healthy = live Fyers candles that are not stale (market hours)."""
    if not state or not state.get("candles"):
        return False, False
    ok = state.get("status") == "live"
    try:
        h, m = _hm(state["last_candle"])
        ok = ok and (t.hour * 60 + t.minute) - (h * 60 + m) <= 5
    except Exception:
        pass
    return True, ok


def run_loop(get_state, stop, notify=None, log=lambda *a: None):
    """Background loop used by tools/stream_server.py. get_state() returns the latest stream state dict."""
    cfg = load_config()
    if not cfg or cfg["mode"] == "off" or not cfg["password"]:
        return
    if notify is None:
        import telegram_notify
        notify = telegram_notify.send
    done_day, bad_since, launched = None, None, False
    while not stop.is_set():
        t = now_ist()
        day = str(t.date())
        try:
            cfg = load_config() or cfg
            if cfg["mode"] != "off" and t.weekday() < 5 and (5, 0) <= (t.hour, t.minute) < _hm(cfg["stop_at"]):
                if not launched:
                    launch_obs(cfg)
                    launched = True
                    stop.wait(25)
                obs = Obs(cfg["host"], cfg["port"], cfg["password"]).connect()
                try:
                    obs.ensure_scene(cfg["scene"], cfg["url"], configure=False)
                    streaming = obs.streaming()
                    ready, healthy = feed_health(get_state(), t)
                    market = (9, 15) <= (t.hour, t.minute) < (15, 30)
                    if market and not healthy:
                        bad_since = bad_since or t
                    else:
                        bad_since = None
                    bad = (t - bad_since).total_seconds() / 60 if bad_since is not None else 0
                    action = decide(cfg, t, streaming, ready, healthy, done_day == day, bad, trading_day=_trading_day(t))
                    if action == "start":
                        obs.call("SetCurrentProgramScene", {"sceneName": cfg["scene"]})      # the broadcast shows the stream page
                        try:                                                                     # reload it, in case it was loaded before the page was up
                            obs.call("PressInputPropertiesButton", {"inputName": cfg["scene"] + " page", "propertyName": "refreshnocache"})
                        except ObsError:
                            pass
                        stop.wait(3)
                        obs.call("StartStream")
                        done_day = day                                          # one attempt a day: a failed start would otherwise pop up OBS's error window every 20 seconds
                        if started_ok(obs):
                            notify(f"BharatPulse: YouTube broadcast STARTED ({t:%H:%M} IST, mode {cfg['mode']}). Each broadcast has a NEW link: open YouTube Studio > Stream, click Share, copy the link and send it to your friends.")
                        else:
                            notify("BharatPulse: the YouTube broadcast did NOT start. " + NO_KEY)
                    elif action in ("stop", "stale-stop"):
                        obs.call("StopStream")
                        done_day = day
                        notify("BharatPulse: YouTube broadcast stopped at " + f"{t:%H:%M} IST" + (" because the data feed was bad for 10 minutes. It stays off until tomorrow." if action == "stale-stop" else " (end of day)."))
                finally:
                    obs.close()
        except Exception as e:
            log("obs:", e)
        stop.wait(20)


if __name__ == "__main__":
    sys.exit("Use tools/obs_setup.py to set up, then start the helper (Refresh BharatPulse.bat).")
