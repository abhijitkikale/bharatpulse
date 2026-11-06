"""BharatPulse live stream: a chart page for OBS / YouTube Live, fed automatically (nothing to paste).

Every 10 seconds in market hours it pulls Nifty 1-minute candles from Fyers; every 5 minutes the current-expiry option chain for the
live GEX levels. End-of-day levels come from the published dashboard page (https://abhijitkikale.github.io/bharatpulse/), with the local
data/dashboard.json as a fallback. The page is served on http://127.0.0.1:8766/ (this PC only): add it in OBS as a Browser source.

Normally 'Refresh BharatPulse.bat' starts this together with the live updates (tools/refresh_app.py). By hand:
  python tools/stream_server.py          run until closed
  python tools/stream_server.py --once   build the state once, print a summary, exit"""
import argparse
import http.server
import json
import pathlib
import sys
import threading
import time

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import fetch_fyers  # noqa: E402
import live_gex  # noqa: E402
import nse_calendar  # noqa: E402
import stream_levels  # noqa: E402
import stream_setups  # noqa: E402
import stream_trades  # noqa: E402

PAGES = "https://abhijitkikale.github.io/bharatpulse/"
PAGE_DIR = ROOT / "tools" / "stream_page"
OUT = ROOT / "data" / "stream_state.json"
RELAY = ROOT / "data" / "stream_relay.json"      # {"topic": "..."}: made by tools/stream_relay_setup.py
RELAY_URL = "https://ntfy.sh/"
PORT = 8766
SYM = "NIFTY"
STORE = ROOT / "data" / "stream_eod"          # one end-of-day levels file per close date, kept so the day's trades can be replayed after the evening build


def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def relay_topic():
    import os
    t = os.environ.get("BHARATPULSE_RELAY_TOPIC")
    if not t and RELAY.exists():
        try:
            t = json.loads(RELAY.read_text(encoding="utf-8")).get("topic")
        except Exception:
            t = None
    return t if t and 12 <= len(t) <= 80 and all(ch.isalnum() or ch in "-_" for ch in t) else None


def market_hours(t=None):
    """Poll fast from 5 minutes before the open to 10 minutes after the close of an NSE session (holidays and special sessions from the calendar)."""
    t = t or now_ist()
    return nse_calendar.in_session(t, lead=5, tail=10)


def save_eod(eod):
    try:
        STORE.mkdir(parents=True, exist_ok=True)
        f = STORE / (eod["close"] + ".json")
        if not f.exists():
            f.write_text(json.dumps(eod), encoding="utf-8")
    except Exception:
        pass


def eod_for(day):
    """The newest saved end-of-day levels whose close date is before `day` (what was valid during that session), or None."""
    try:
        files = sorted(f for f in STORE.glob("*.json") if f.stem < str(day))
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
    except Exception:
        return None


class Feed:
    def __init__(self):
        self.df = None
        self.eod = None
        self.eod_at = 0.0
        self.live = None
        self.live_at = 0.0
        self.chains = None                             # latest current-expiry chain rows (they carry the option symbols)
        self.reported = None                           # the day the morning 'ready' check was sent to Telegram
        self.tv = {}                                   # opening 1-minute candles sent by the TradingView alert: {IST minute: bar}
        self.opt = {}                                  # (strike, "CE" / "PE") -> (fetched at, candles)
        self.state = None
        self.msg = "starting"
        self.lock = threading.Lock()

    def load_eod(self):
        D = None
        try:
            D = stream_levels.parse_pages(requests.get(PAGES, timeout=40).text)
        except Exception:
            try:
                D = json.loads((ROOT / "data" / "dashboard.json").read_text(encoding="utf-8"))
            except Exception:
                pass
        if D:
            try:
                self.eod = stream_levels.eod_levels(D, SYM)
                self.eod_at = time.time()
                save_eod(self.eod)
            except Exception:
                pass

    def step(self):
        t = now_ist()
        if self.eod is None or time.time() - self.eod_at > 900:
            self.load_eod()
        frames, st = fetch_fyers.fetch_intraday(days=8, symbols=(SYM,))
        if frames.get(SYM) is not None:
            self.df = frames[SYM]
            self.msg = "live" if market_hours(t) else "market closed"
        else:
            self.msg = "Fyers sign-in needed (data shown is the last update)" if self.df is not None else "waiting for Fyers sign-in"
        if self.df is None:
            return
        day = self.df.index.max().date()
        df, used = stream_levels.merge_open(self.df, {k: v for k, v in list(self.tv.items()) if k.date() == day})
        if self.chains is None or (market_hours(t) and time.time() - self.live_at >= 300):
            try:
                chains, _ = fetch_fyers.fetch_chains(current_only=True, symbols=(SYM,))
                if chains:
                    self.chains = chains
                if market_hours(t) and chains:
                    g = live_gex.build_gex(chains, t)
                    if g and SYM in g:
                        self.live = {**g[SYM], "time": t.strftime("%H:%M")}
                        self.live_at = time.time()
            except Exception:
                pass
        setups, replay = None, None
        try:                                           # CALL / PUT setups on today's candles (only once the end-of-day levels are for today)
            today = df[df.index.date == df.index.max().date()]
            sday = today.index.max().date()
            # the levels that were valid during that session: the newest ones if they are older than the session, else (after the evening build) the saved ones
            replay = self.eod if self.eod and self.eod["close"] < str(sday) else eod_for(sday)
            if replay:
                daily, weekly = stream_levels.vwaps(df)
                setups = stream_setups.find_setups(today, replay["levels"], daily, weekly, stream_setups.developing_va(today), SYM)
        except Exception:
            setups = None
        trades = None
        if setups:
            try:
                res = stream_trades.run_trades(setups, self.option_frame)
                trades = stream_trades.to_json(res, stream_levels.SHIFT)
            except Exception:
                trades = None
        state = stream_levels.build_state(df, self.eod, self.live, t, self.msg, setups=setups, trades=trades)
        state["open_src"] = used                        # how many opening candles came from TradingView
        state["replay_eod"] = replay["close"] if setups is not None and replay is not self.eod else None   # the trades use the levels of this older close
        self.report(t, used)
        with self.lock:
            self.state = state
        OUT.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")

    def report(self, t, used):
        """Once a day at 09:31 IST: tell Telegram whether the stream is ready (one look instead of checking every screen)."""
        hm = (t.hour, t.minute)
        if self.reported == str(t.date()) or t.weekday() >= 5 or not ((9, 31) <= hm < (15, 30)):
            return
        self.reported = str(t.date())
        try:
            import telegram_notify
            fy = self.msg == "live"
            if relay_topic():
                tv = f"TradingView ({used} candles)" if used else "NOT received: the Fyers opening candle is used, so the VWAP may differ early (check the TradingView alert)"
            else:
                tv = "not set up (Fyers opening candle)"
            lines = ["BharatPulse stream: " + ("READY" if fy else "CHECK THE FYERS SIGN-IN"),
                     f"Fyers candles: {self.msg} (last {self.df.index.max():%H:%M})" if self.df is not None else "Fyers candles: none",
                     "Live GEX: " + (f"ok ({self.live['time']})" if self.live else "not yet"),
                     "Opening candles: " + tv,
                     "End-of-day levels: " + (f"from the {self.eod['close']} close" if self.eod else "not loaded"),
                     f"Page: http://127.0.0.1:{PORT}/"]
            telegram_notify.send("\n".join(lines))
        except Exception:
            pass

    def option_frame(self, s):
        """1-minute candles of the contract a setup would buy (at-the-money strike of the nearest expiry), cached for 15 seconds."""
        typ = "CE" if s["side"] == "CALL" else "PE"
        key = (float(s["strike"]), typ)
        hit = self.opt.get(key)
        if hit and time.time() - hit[0] < 15:
            return hit[1]
        rows = (self.chains or {}).get(SYM, {}).get("rows") or []
        if not rows:
            return hit[1] if hit else None
        exp = min(r["XpryDt"] for r in rows)
        sym = next((r.get("symbol") for r in rows if r["XpryDt"] == exp and r["StrkPric"] == key[0] and r["OptnTp"] == typ), None)
        df = fetch_fyers.fetch_option_candles(sym) if sym else None
        if df is None and hit:
            df = hit[1]                                 # keep the last good candles if this call failed
        self.opt[key] = (time.time(), df)
        return df

    def relay(self, stop):
        """Listen for the TradingView alert messages on the relay topic (outbound connection only) and keep the opening candles."""
        topic = relay_topic()
        if not topic:
            return
        wait = 5
        while not stop.is_set():
            try:
                with requests.get(RELAY_URL + topic + "/json", params={"since": "14h"}, stream=True, timeout=(10, 120)) as r:
                    r.raise_for_status()
                    wait = 5
                    for line in r.iter_lines():
                        if stop.is_set():
                            break
                        try:
                            ev = json.loads(line) if line else None
                        except ValueError:
                            continue
                        if ev and ev.get("event") == "message":
                            got = stream_levels.parse_open_bar(ev.get("message", ""), now_ist().date())
                            if got:
                                self.tv[got[0]] = got[1]
            except Exception:
                pass
            stop.wait(wait)
            wait = min(wait * 2, 120)

    def run(self, stop):
        while not stop.is_set():
            try:
                self.step()
            except Exception as e:                     # keep serving the last good state
                self.msg = "update failed: " + type(e).__name__
            stop.wait(10 if market_hours() else 120)


FEED = Feed()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/state.json":
            with FEED.lock:
                s = FEED.state
            body = json.dumps(s if s else {"status": FEED.msg, "candles": [], "levels": []}, separators=(",", ":")).encode()
            return self._send(200, body, "application/json")
        if path == "/calendar.json":
            try:
                body = json.dumps(nse_calendar.to_json(), separators=(",", ":")).encode()
            except Exception:
                body = b"{}"
            return self._send(200, body, "application/json")
        if path in ("/", "/index.html"):
            return self._send(200, (PAGE_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        self._send(404, b"not found", "text/plain")


_STARTED = {"on": False}


def start_in_thread(stop=None, port=None):
    """Start the feed and the page server in background threads (safe to call twice). Used by tools/refresh_app.py."""
    if _STARTED["on"]:
        return
    _STARTED["on"] = True
    stop = stop or threading.Event()
    threading.Thread(target=FEED.run, args=(stop,), daemon=True).start()
    threading.Thread(target=FEED.relay, args=(stop,), daemon=True).start()
    try:                                                # OBS and the YouTube broadcast (only when data/obs.json is set up, see tools/obs_setup.py)
        import obs_control
        threading.Thread(target=obs_control.run_loop, args=(lambda: FEED.state, stop), daemon=True).start()
    except Exception:
        pass
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port or PORT), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    except OSError:
        pass                                          # the port is already served by another copy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    a = ap.parse_args()
    if a.once:
        FEED.step()
        s = FEED.state
        print(FEED.msg, "| candles", len(s["candles"]) if s else 0, "| last", s and s["last"], "| levels", s and len(s["levels"]))
        for l in (s or {}).get("levels", []):
            print(f"  {l['kind']:4} {l['name']:16} {l['value']}")
        print("regime", s and s["regime"])
        return
    print(f"BharatPulse stream page: http://127.0.0.1:{a.port}/   (add it in OBS as a Browser source; close this window to stop)")
    start_in_thread(port=a.port)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
