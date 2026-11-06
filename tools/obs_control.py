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
import websocket                     # websocket-client

ROOT = pathlib.Path(__file__).resolve().parent.parent
CFG = ROOT / "data" / "obs.json"
EXES = [r"C:\Program Files\obs-studio\bin\64bit\obs64.exe", r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"]
DEFAULTS = {"host": "127.0.0.1", "port": 4455, "password": "", "mode": "off", "scene": "BharatPulse", "url": "http://127.0.0.1:8766/?clean=1",
            "stop_at": "15:35", "exe": ""}


class ObsError(Exception):
    pass


class Obs:
    """Minimal OBS WebSocket v5 client."""

    def __init__(self, host="127.0.0.1", port=4455, password="", timeout=10):
        self.host, self.port, self.password, self.timeout, self.ws = host, int(port), password, timeout, None

    def connect(self):
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

    def ensure_scene(self, scene, url):
        """Create the scene and its Browser source (1920x1080, the stream page) if missing; update the address if it exists."""
        if scene not in [x["sceneName"] for x in self.call("GetSceneList").get("scenes", [])]:
            self.call("CreateScene", {"sceneName": scene})
        name = scene + " page"
        settings = {"url": url, "width": 1920, "height": 1080, "shutdown": False, "restart_when_active": False}
        if name not in [x["inputName"] for x in self.call("GetInputList").get("inputs", [])]:
            self.call("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": "browser_source", "inputSettings": settings, "sceneItemEnabled": True})
        else:
            self.call("SetInputSettings", {"inputName": name, "inputSettings": settings, "overlay": True})
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


def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def _hm(text):
    h, m = str(text).split(":")
    return int(h), int(m)


def decide(cfg, t, streaming, ready, healthy, done, bad_minutes):
    """The next action: 'start', 'stop', 'stale-stop' or None.
    ready: the stream page has data; healthy: the market-hours feed is fine; done: a broadcast was already started or stopped today;
    bad_minutes: how long the feed has been bad during market hours."""
    hm, stop_hm = (t.hour, t.minute), _hm(cfg["stop_at"])
    if t.weekday() >= 5:
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


def launch_obs(cfg):
    """Start OBS minimised (no 'safe mode' prompt) if it is not running. Returns True when it was started."""
    if obs_running():
        return False
    exe = next((e for e in ([cfg.get("exe")] if cfg.get("exe") else []) + EXES if pathlib.Path(e).exists()), None)
    if not exe:
        return False
    subprocess.Popen([exe, "--minimize-to-tray", "--disable-shutdown-check"], cwd=str(pathlib.Path(exe).parent))
    return True


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
                    obs.ensure_scene(cfg["scene"], cfg["url"])
                    streaming = obs.streaming()
                    ready, healthy = feed_health(get_state(), t)
                    market = (9, 15) <= (t.hour, t.minute) < (15, 30)
                    if market and not healthy:
                        bad_since = bad_since or t
                    else:
                        bad_since = None
                    bad = (t - bad_since).total_seconds() / 60 if bad_since is not None else 0
                    action = decide(cfg, t, streaming, ready, healthy, done_day == day, bad)
                    if action == "start":
                        obs.call("StartStream")
                        done_day = day
                        notify(f"BharatPulse: YouTube broadcast STARTED ({t:%H:%M} IST, mode {cfg['mode']}).")
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
