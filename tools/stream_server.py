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
import stream_journal  # noqa: E402
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
OPEN_DIR = ROOT / "data" / "stream_open"      # the TradingView opening candles, one file per day (the relay forgets them after 12 hours)
SIG_DIR = ROOT / "data" / "stream_sig"          # the setups TradingView reported through the alert, one file per day (kept for comparison and for signals_from=tradingview)
JOURNAL = ROOT / "data" / "journal"            # trading journal backup: one file per day plus trades.csv (this PC only)
EXP_FILE = ROOT / "data" / "expiry_by_day.json"   # the option expiry used for each session day (kept for replays after the expiry has passed)
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


def signals_from_tradingview():
    """True (the default) when the page books its paper trades from the setups TradingView reports. data/stream_relay.json {"signals_from": "fyers"}
    makes the page find the setups itself on the Fyers candles instead (the TradingView setups are then only recorded for comparison).
    The BP Live alert is renewed every morning by tools/tv_live_paste.py so that it carries that day's level text."""
    try:
        return json.loads(RELAY.read_text(encoding="utf-8")).get("signals_from") != "fyers"
    except Exception:
        return True


def market_hours(t=None):
    """Poll fast from 5 minutes before the open to 10 minutes after the close of an NSE session (holidays and special sessions from the calendar)."""
    t = t or now_ist()
    return nse_calendar.in_session(t, lead=5, tail=10)


def journal_frame(day, key):
    """The option candles saved in that day's journal for a contract (strike, 'CE' / 'PE'): from each trade's entry to its exit. None if there are none."""
    try:
        doc = json.loads((JOURNAL / f"{day}.json").read_text(encoding="utf-8"))
        rows = {}
        for tr in doc.get("trades", []):
            if (float(tr["strike"]), "CE" if tr["side"] == "CALL" else "PE") == key:
                for hm, o, h, l, c, v in tr.get("option_candles_hm_ohlcv", []):
                    rows[pd.Timestamp(f"{day} {hm}", tz="Asia/Kolkata")] = [o, h, l, c, v]
        if not rows:
            return None
        return pd.DataFrame.from_dict(rows, orient="index", columns=["o", "h", "l", "c", "v"]).sort_index()
    except Exception:
        return None


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
        self.sent_day, self.sent = None, set()          # Telegram trade messages already sent today
        self.reported = None                           # the day the morning 'ready' check was sent to Telegram
        self.sigs = self.load_sigs()                   # setups reported by TradingView: {date: [setup dicts]}
        self.tv = self.load_tv()                       # opening 1-minute candles sent by the TradingView alert: {IST minute: bar}
        self.opt = {}                                  # (strike, "CE" / "PE") -> (fetched at, candles)
        self.meta = {}                                 # (strike, "CE" / "PE") -> {"symbol", "expiry"} of the contract used
        self._jkey, self._jat = None, 0.0               # the last journal content written and when
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
        if setups is not None and signals_from_tradingview():     # book the paper trades from the setups TradingView reported
            setups = list(self.sigs.get(str(sday), []))
        trades, res = None, None
        if setups:
            try:
                res = stream_trades.run_trades(setups, self.option_frame)
                trades = stream_trades.to_json(res, stream_levels.SHIFT)
            except Exception:
                trades = None
            self.journal(sday, replay, today, res, used, state_regime=None)
            self.alerts(t, sday, res)
        state = stream_levels.build_state(df, self.eod, self.live, t, self.msg, setups=setups, trades=trades)
        state["open_src"] = used                        # how many opening candles came from TradingView
        state["replay_eod"] = replay["close"] if setups is not None and replay is not self.eod else None   # the trades use the levels of this older close
        self.report(t, used)
        with self.lock:
            self.state = state
        OUT.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")

    @staticmethod
    def load_sigs():
        out = {}
        try:
            for f in sorted(SIG_DIR.glob("*.json"))[-6:]:
                rows = json.loads(f.read_text(encoding="utf-8"))
                out[f.stem] = [{**r, "time": pd.Timestamp(r["time"])} for r in rows]
        except Exception:
            pass
        return out

    def add_sig(self, sg):
        """Keep one setup reported by TradingView (the relay replays old messages after a restart, so a repeat is ignored)."""
        day = str(sg["time"].date())
        lst = self.sigs.setdefault(day, [])
        if any(x["time"] == sg["time"] and x["side"] == sg["side"] and x["level"] == sg["level"] and x["tag"] == sg["tag"] for x in lst):
            return
        lst.append(sg)
        lst.sort(key=lambda x: x["time"])
        try:
            SIG_DIR.mkdir(parents=True, exist_ok=True)
            (SIG_DIR / f"{day}.json").write_text(json.dumps([{**x, "time": x["time"].isoformat()} for x in lst]), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def load_tv():
        """The saved opening candles of the last few days."""
        out = {}
        try:
            for f in sorted(OPEN_DIR.glob("*.json"))[-6:]:
                for hm, b in json.loads(f.read_text(encoding="utf-8")).items():
                    out[pd.Timestamp(f"{f.stem} {hm}", tz="Asia/Kolkata")] = b
        except Exception:
            pass
        return out

    def save_tv(self, day):
        try:
            OPEN_DIR.mkdir(parents=True, exist_ok=True)
            rows = {k.strftime("%H:%M"): v for k, v in list(self.tv.items()) if k.date() == day}
            (OPEN_DIR / f"{day}.json").write_text(json.dumps(rows), encoding="utf-8")
        except Exception:
            pass

    def journal(self, sday, replay, today, res, used, state_regime=None):
        """Keep the trading journal of this session up to date (at most once a minute while the market is open, and whenever it changes after that)."""
        if not res or (not res["trades"] and not res["signals"]):
            return
        try:
            doc = stream_journal.build_day(sday, res, replay, today, {k: v[1] for k, v in self.opt.items() if v[1] is not None}, self.meta,
                                           {"opening_candles": f"TradingView ({used} candles)" if used else "Fyers", "live_gex_last": self.live,
                                            "eod_levels_close_shown_on_page": self.eod["close"] if self.eod else None})
            key = json.dumps(doc, sort_keys=True, default=str)
            if key == self._jkey or (market_hours() and time.time() - self._jat < 60):
                return
            stream_journal.write(doc, JOURNAL)
            self._jkey, self._jat = key, time.time()
        except Exception:
            pass

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

    def alerts(self, t, sday, res):
        """Telegram: a message for each new paper trade, its close and the setups not taken (only today's live session; sent keys are saved so a restart does not repeat them)."""
        try:
            if sday != t.date() or not market_hours(t):
                return
            import stream_alerts, telegram_notify
            f = ROOT / "data" / "telegram_sent.json"
            if self.sent_day != str(sday):
                self.sent_day, self.sent = str(sday), set()
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                    if d.get("day") == str(sday):
                        self.sent = set(d.get("keys", []))
                except Exception:
                    pass
            before = len(self.sent)
            for text in stream_alerts.new_messages(res, self.sent, t):
                telegram_notify.send_alert(text)
            if len(self.sent) != before:
                f.write_text(json.dumps({"day": str(sday), "keys": sorted(self.sent)}), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def fyers_symbol(day_exp, strike, typ):
        """NSE:NIFTY26N0925050CE style weekly contract name (year, month letter / digit, day, strike, CE / PE)."""
        d = pd.Timestamp(day_exp)
        m = "123456789OND"[d.month - 1]
        return f"NSE:{SYM}{d:%y}{m}{d:%d}{int(strike)}{typ}"

    def exp_load(self):
        try:
            return json.loads(EXP_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def exp_save(self, day, exp):
        d = self.exp_load()
        if d.get(str(day)) != exp:
            d[str(day)] = exp
            try:
                EXP_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")
            except OSError:
                pass

    def contract(self, day, key, rows):
        """(symbol, expiry, candles) of the contract a setup of session `day` would buy: the nearest expiry that was valid ON THAT DAY, not today's
        (after an expiry the chain only lists later ones, so a replay of an older session would otherwise price the wrong contract)."""
        typ = key[1]
        known = self.exp_load().get(str(day))
        rmin = str(min(r["XpryDt"] for r in rows))[:10] if rows else None
        if known is None and rmin is not None and (rmin <= str(day) or day == now_ist().date()):
            known = rmin                                 # the chain still lists that session's own nearest expiry
            self.exp_save(day, known)
        if known:
            sym = next((r.get("symbol") for r in rows if str(r["XpryDt"])[:10] == known and r["StrkPric"] == key[0] and r["OptnTp"] == typ), None)                 or self.fyers_symbol(known, key[0], typ)
            return sym, known, fetch_fyers.fetch_option_candles(sym, day)
        # unknown and the chain has moved on: try the session day itself and the days after it, up to the chain's nearest expiry
        d0 = pd.Timestamp(day)
        for k in range(0, 8):
            e = (d0 + pd.Timedelta(days=k)).strftime("%Y-%m-%d")
            if rmin is not None and e >= rmin:
                break
            sym = self.fyers_symbol(e, key[0], typ)
            df = fetch_fyers.fetch_option_candles(sym, day)
            if df is not None:
                self.exp_save(day, e)
                return sym, e, df
        return None, None, None

    def option_frame(self, s):
        """1-minute candles of the contract a setup would buy (at-the-money strike of the nearest expiry of THAT session), cached for 15 seconds."""
        typ = "CE" if s["side"] == "CALL" else "PE"
        key = (float(s["strike"]), typ)
        hit = self.opt.get(key)
        if hit and time.time() - hit[0] < 15:
            return hit[1]
        day = s["time"].date()                          # the session being replayed (not necessarily today's date)
        rows = (self.chains or {}).get(SYM, {}).get("rows") or []
        sym, exp, df = self.contract(day, key, rows)
        if sym:
            self.meta[key] = {"symbol": sym, "expiry": exp}
        if df is None and hit:
            df = hit[1]                                 # keep the last good candles if this call failed
        if df is None:
            df = journal_frame(day, key)                # Fyers has no candles for that day any more: use the ones saved in the journal
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
                            sg = stream_levels.parse_signal(ev.get("message", ""), now_ist().date())
                            if sg:
                                self.add_sig(sg)
                                continue
                            got = stream_levels.parse_open_bar(ev.get("message", ""), now_ist().date(), max_days=4)
                            if got:
                                self.tv[got[0]] = got[1]
                                self.save_tv(got[0].date())
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
        if path in ("/journal", "/journal/"):
            try:
                import journal_dashboard
                return self._send(200, journal_dashboard.build_html().encode("utf-8"), "text/html; charset=utf-8")
            except Exception as e:
                return self._send(500, ("journal page failed: " + str(e)).encode("utf-8"), "text/plain; charset=utf-8")
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
