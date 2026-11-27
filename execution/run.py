"""The runner: ties the dashboard's trade objects to the engines, the order WebSocket and the status page. One process, PAPER by default.

Every cycle (cfg cycle_s, default 3 s): read the dashboard's trades -> the PAPER engine mirrors them (only while the mode is PAPER) -> the LIVE engine, if LIVE was armed in this
session, executes them at FYERS (only while the mode is LIVE; it keeps managing an open position after LIVE is switched off) -> positions are reconciled with the broker every
reconcile_every_s. LIVE can only be armed from the status page / typed phrase (mode.py)."""
import datetime as dt
import json
import pathlib
import sys
import threading
import time

from . import config, mode as modemod, trade_source
from .brokers import FyersBroker, PaperBroker
from .engine import Engine, IST, now_ist
from .instrument import Instruments
from .order_feed import OrderFeed
from .store import Store
from .tick_feed import TickFeed, load_data_token


def _paused_fn():
    def paused(now):
        try:
            iv = json.loads(config.PAUSE_FILE.read_text(encoding="utf-8")).get("intervals", [])
        except (OSError, ValueError):
            return False
        for s, e in iv:
            try:
                a = dt.datetime.fromisoformat(s)
                a = a if a.tzinfo else a.replace(tzinfo=IST)
                b = dt.datetime.fromisoformat(e) if e else None
                b = (b if b is None or b.tzinfo else b.replace(tzinfo=IST))
                if a.date() == now.date() and now >= a and (b is None or now < b):
                    return True
            except ValueError:
                continue
        return False
    return paused


def _lot_from_dashboard():
    try:
        return int(json.loads(config.JOURNAL_SETTINGS.read_text(encoding="utf-8")).get("lot"))
    except Exception:
        return None


def _levels_of(rec):
    """The engine's own stop / target of an open Automate trade (measured from the real fill), for the status page; None when it has none."""
    try:
        return json.loads(rec["extra_json"] or "{}").get("lv")
    except (TypeError, ValueError, KeyError):
        return None


def make_notifier(log_dir=None):
    """Prints, writes a JSON line to execution/logs/<day>.jsonl and sends LIVE messages and every warning to the private Telegram chat (through the dashboard's existing notifier)."""
    log_dir = pathlib.Path(log_dir or config.LOGS)

    def notify(text, level="info"):
        line = f"{now_ist():%H:%M:%S} {text}"
        print(line, flush=True)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / f"{now_ist():%Y-%m-%d}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": now_ist().isoformat(timespec="seconds"), "level": level, "text": text}) + "\n")
        except OSError:
            pass
        if text.startswith("[LIVE]") or level in ("warn", "critical"):
            try:
                sys.path.insert(0, str(config.ROOT / "tools"))
                import telegram_notify
                telegram_notify.send(text)
            except Exception:
                pass
    return notify


class Runner:
    def __init__(self, cfg=None, store=None, dash_path=None, notify=None, instruments=None, broker_factory=None, feed_factory=None, tick_factory=None):
        self.cfg = cfg or config.load()
        self.store = store or Store()
        self.dash_path = dash_path
        self.notify = notify or make_notifier()
        self.instruments = instruments or Instruments()
        self.mode = modemod.ModeManager()
        self.paper = Engine("PAPER", PaperBroker(), self.store, self.cfg, self.notify, instruments=self.instruments, paused=_paused_fn())
        self.paper.lot_fallback = _lot_from_dashboard()
        self.live = None
        self.broker_factory = broker_factory or (lambda app, tok, cfg: FyersBroker(app, tok, cfg))
        self.feed_factory = feed_factory or OrderFeed
        self.feed = None
        self.ticks = None                                        # live option prices (Automate executor): execution/tick_feed.py
        self.tick_state = {}
        self.tick_factory = tick_factory or TickFeed
        self.last_recon = 0.0
        self.problems = []
        self.dash_fresh = False
        self.invalid_told = set()
        self.lock = threading.RLock()

    # ---- mode control (called from the status page) ----
    def arm(self, phrase, now=None):
        with self.lock:
            now = now or now_ist()
            ok, msg = self.mode.arm(phrase, now)
            if not ok:
                return False, msg
            app_id, token = modemod.load_token(now)
            broker = self.broker_factory(app_id, token, self.cfg)
            prof = broker.profile()
            if prof is None:
                self.mode.disarm("the FYERS profile call failed (token, network or the registered IP)")
                return False, "LIVE not activated: the FYERS profile call failed (token, network or registered IP). Run `python -m execution check`."
            automate = self.cfg.get("executor") == "automate"
            if automate:
                from . import automate as automod
                ac = automod.load_config()
                if not ac["slots"]:
                    self.mode.disarm("no Automate workflow is configured")
                    return False, "LIVE not activated: executor is 'automate' but execution/automate.json has no valid slots (see execution/AUTOMATE.md)."
            if self.live is None or (self.live.mode == "AUTOMATE") != automate:
                if self.live is not None and self.store.active(self.live.mode):
                    self.mode.disarm("the executor was changed while a trade is open")
                    return False, "LIVE not activated: a trade is still open under the other executor. Close it first (or restart the engine)."
                if automate:
                    self.live = automod.AutomateEngine(broker, self.store, self.cfg, self.notify, instruments=self.instruments, paused=_paused_fn(), slots_cfg=ac, ticks=self._start_ticks())
                else:
                    self.live = Engine("LIVE", broker, self.store, self.cfg, self.notify, instruments=self.instruments, paused=_paused_fn())
            else:
                self.live.b = broker
                if automate:
                    self.live.ac = ac
            if self.cfg.get("ws_enabled") and self.feed is None and not automate:
                self.feed = self.feed_factory(app_id, token, self.live.on_order_event, self.live.ws_state, log=self.notify)
                self.feed.start()
            self.live.reconcile(now)
            how = (f"through FYERS Automate ({len(ac['slots'])} workflow(s); lots are set in the workflows)" if automate else f"to FYERS: {self.cfg['lots']} lot(s) per trade")
            self.notify(f"[LIVE] LIVE mode ACTIVATED by you at {now:%H:%M:%S}. Dashboard trades are now sent {how}.", "warn")
            return True, msg

    def _start_ticks(self):
        """The live price feed for the Automate executor (market-data token). None when switched off or when there is no data token: the engine then uses the broker's price and the dashboard."""
        if not self.cfg.get("tick_feed"):
            return None
        if self.ticks is None:
            tok = load_data_token()
            if tok is None:
                self.tick_state.update(connected=False, error="no market-data token (data/fyers_token.json)")
                return None
            feed = self.tick_factory(tok[0], tok[1], self.tick_state, log=self.notify)
            if not feed.start():
                return None
            self.ticks = feed
        return self.ticks

    def disarm(self, why="switched off by you"):
        with self.lock:
            ok, msg = self.mode.disarm(why)
            self.notify(f"LIVE switched off ({why}): no new orders; an open position keeps its stop-loss and is still managed.", "warn")
            return ok, msg

    def emergency(self, why="emergency exit"):
        """Stop LIVE, write the KILL file, cancel any unfilled entry and exit the open position (stop cancelled and confirmed first)."""
        with self.lock:
            if self.live is not None:
                self.live.request_flatten()
            self.mode.hard_kill(why)
            self.notify(f"EMERGENCY: {why}. LIVE is OFF (KILL file written), unfilled entries are being cancelled and the open position is being exited.", "critical")
            return True, "emergency exit started"

    # ---- one cycle ----
    def cycle(self, now=None):
        with self.lock:
            now = now or now_ist()
            day = now.date().isoformat()
            if modemod.KILL.exists() and self.mode.live:
                self.mode.disarm("KILL file present")
            intents, fresh, problems = trade_source.read(day, self.dash_path, self.cfg["stale_state_s"])
            self.dash_fresh, self.problems = fresh, problems
            for p in problems:
                if p not in self.invalid_told and "old" not in p:
                    self.invalid_told.add(p)
                    self.notify(f"dashboard trade not usable: {p}", "warn")
            self.paper.step(intents, now, accept_new=not self.mode.live, dash_fresh=fresh)
            if self.live is not None:
                tok = modemod.load_token(now)
                self.live.step(intents, now, accept_new=self.mode.live, dash_fresh=fresh, auth_ok=tok is not None)
                if time.time() - self.last_recon >= self.cfg["reconcile_every_s"]:
                    self.last_recon = time.time()
                    self.live.reconcile(now)

    def status(self, now=None):
        now = now or now_ist()
        day = now.date().isoformat()
        tok = modemod.load_token(now)
        broker = self.live.b if self.live else None
        last_ok = getattr(broker, "last_ok", 0.0) if broker else 0.0
        eng = self.live if (self.live is not None and (self.mode.live or self.store.active(self.live.mode))) else self.paper
        snap = eng.snapshot(day)
        cur = snap["current"]
        it = None
        if cur:
            it = eng.last_intents.get(cur["signal_id"]) or eng._intent_from_rec(cur)
        return {
            "now": now.isoformat(timespec="seconds"), "executor": self.cfg.get("executor", "api"), "mode": self.mode.mode, "note": self.mode.note, "live_checks": [{"ok": ok, "text": t} for ok, t in self.mode.checks(now)],
            "auth": {"ok": tok is not None, "text": "trading-app token valid for today" if tok else "no valid trading-app token (run tools/fyers_order_login.py after 06:30 IST)"},
            "connection": {"ok": bool(last_ok and time.time() - last_ok < 60), "text": ("FYERS reachable" if last_ok and time.time() - last_ok < 60 else "no recent answer from FYERS" if self.live else "LIVE not armed: no FYERS session")},
            "ws": snap["ws"], "ticks": dict(self.tick_state), "dashboard": {"fresh": self.dash_fresh, "problems": self.problems[-5:]},
            "engine": snap["mode"], "halted": snap["halted"], "recon": snap["recon"], "entries_today": snap["entries_today"], "realized": snap["realized"], "unrealized": snap["unrealized"],
            "current": None if not cur else {"levels": _levels_of(cur), "phase": cur["phase"], "symbol": cur["symbol"], "qty": cur["qty_filled"] or cur["qty_req"], "avg_entry": cur["avg_entry"], "sl_stop": cur["sl_stop"],
                                             "sl_limit": cur["sl_limit"], "sl_order": cur["sl_order_id"], "exit_reason": cur["exit_reason"],
                                             "dashboard": None if it is None else {"entry": it.entry, "stop_now": it.stop_now, "target": it.target, "status": it.status, "last": it.last}},
            "signals": snap["signals"][-12:], "events": [{"t": dt.datetime.fromtimestamp(e["ts"], IST).strftime("%H:%M:%S"), "level": e["level"], "text": e["text"], "mode": e["mode"]} for e in self.store.events(None, 25)],
            "config": {k: self.cfg[k] for k in ("lots", "entry_cushion_pct", "sl_limit_gap_pct", "exit_cushion_pct", "entry_wait_s", "max_entries_per_day", "max_daily_loss_inr", "square_off")},
        }

    def run_forever(self, stop=None):
        stop = stop or threading.Event()
        self.notify("execution engine started in PAPER mode (LIVE must be activated by you on the status page).")
        while not stop.is_set():
            try:
                self.cycle()
            except Exception as e:
                self.notify(f"execution cycle error: {type(e).__name__}: {str(e)[:120]}", "warn")
            end = time.time() + self.cfg["cycle_s"]
            while not stop.is_set() and time.time() < end:                    # between the full passes: the live price against the stop / target of an open Automate trade, twice a second
                stop.wait(min(0.5, max(0.0, end - time.time())))
                live = self.live
                if live is not None and hasattr(live, "fast_check") and getattr(live, "ticks", None) is not None:
                    try:
                        with self.lock:
                            live.fast_check(now_ist())
                    except Exception as e:
                        self.notify(f"live-price check error: {type(e).__name__}: {str(e)[:120]}", "warn")
