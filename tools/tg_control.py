"""Control BharatPulse from your phone: commands to the Telegram bot (GEXINDICATORBOT), answered only for YOU.

  /status       what is running: Fyers sign-in, live updates, stream page, YouTube broadcast, today's trades
  /orders_off   switch the order router OFF (an open broker stop-loss order stays)     /orders_dry   dry run only: nothing is sent. There is no command for "live".
  /pause        stop NEW entry signals (an open trade carries on)       /resume   allow them again
  /stream_on    start the YouTube broadcast now          /stream_off   stop it (it stays off for the rest of the day)
  /exec_status  the FYERS execution engine: mode, trade, P&L     /exec_start  start the engine (PAPER)     /exec_off  switch LIVE off (an open position keeps its stop)
  /exec_live    ACTIVATE LIVE real orders (then /confirm)     /exec_exit  EMERGENCY: cancel unfilled entries, exit the open position, LIVE off, KILL file (then /confirm)
  /sleep        put this PC to sleep                     /shutdown     shut this PC down (after 30 seconds)
  /confirm      needed once after /sleep or /shutdown (within 60 seconds)     /cancel   cancel it, also an announced shutdown
  /help         this list

Safety: a message is only obeyed when it comes from your own private chat with the bot (the chat id saved in data/telegram.json by
tools/telegram_setup.py). Messages from anyone else, from groups and from channels are ignored. Commands sent while the helper was off are never
run later (old messages are skipped when it starts, and messages older than 2 minutes are dropped). A sleeping PC cannot be woken from Telegram."""
import json
import pathlib
import subprocess
import sys
import threading
import time

import pandas as pd
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pipeline"))
import telegram_notify  # noqa: E402
import order_manager  # noqa: E402
import trading_pause  # noqa: E402

COMMANDS = [("status", "What is running"), ("orders_off", "Switch the order router OFF"), ("orders_dry", "Order router: dry run only"), ("pause", "Pause new entry signals"), ("resume", "Allow new entry signals again"), ("stream_on", "Start the YouTube broadcast"), ("stream_off", "Stop the YouTube broadcast"),
            ("exec_status", "Execution engine: mode, trade, P&L"), ("exec_start", "Start the execution engine (PAPER)"), ("exec_off", "Switch LIVE execution off"), ("exec_live", "ACTIVATE LIVE execution (then /confirm)"), ("exec_exit", "EMERGENCY exit all (then /confirm)"),
            ("sleep", "Put the PC to sleep"), ("shutdown", "Shut the PC down"), ("confirm", "Confirm sleep, shutdown, live or exit"), ("cancel", "Cancel"), ("help", "List the commands")]
CONFIRM_SECONDS = 60
MAX_AGE = 120


def parse(update, owner_chat, now=None):
    """The command (without the slash, lower case) when `update` is a message from the owner's private chat, else None. Pure function."""
    m = update.get("message")
    if not m:                                                  # channel posts, edited messages, membership changes: never commands
        return None
    chat, frm = m.get("chat") or {}, m.get("from") or {}
    if chat.get("type") != "private" or str(chat.get("id")) != str(owner_chat) or str(frm.get("id")) != str(owner_chat):
        return None
    if now is not None and now - m.get("date", 0) > MAX_AGE:
        return None
    text = (m.get("text") or "").strip()
    if not text.startswith("/"):
        return None
    return text.split()[0][1:].split("@")[0].lower()


class Control:
    def __init__(self, status_fn, owner_chat, say):
        self.status_fn, self.owner, self.say = status_fn, owner_chat, say
        self.pending = None                                    # (action, expires)

    def handle(self, cmd, now=None):
        """Run one command; returns nothing, replies through self.say."""
        now = now if now is not None else time.time()
        if self.pending and now > self.pending[1]:
            self.pending = None
        if cmd in ("help", "start"):
            return self.say("Commands: " + ", ".join("/" + c for c, _ in COMMANDS) + ".\n" + "\n".join(f"/{c}: {d}" for c, d in COMMANDS))
        if cmd == "status":
            return self.say(self.status_fn())
        if cmd == "orders_off":
            order_manager.save_mode("off")
            return self.say("Order router is OFF. No new order will be sent. A stop-loss order already at the broker stays there; check the Fyers app.")
        if cmd == "orders_dry":
            order_manager.save_mode("dry_run")
            return self.say("Order router is in DRY RUN: it tells you what it would do and sends nothing.")
        if cmd == "pause":
            new = trading_pause.pause(pd.Timestamp.now(tz="Asia/Kolkata"))
            return self.say("New entry signals are PAUSED until you send /resume (today only). An open trade keeps its stop, target and trailing moves." if new else "Already paused. /resume allows new signals again.")
        if cmd == "resume":
            ok = trading_pause.resume(pd.Timestamp.now(tz="Asia/Kolkata"))
            return self.say("New entry signals are ON again." if ok else "Nothing was paused.")
        if cmd == "stream_on":
            return self._bg(self._stream_on)
        if cmd == "stream_off":
            return self._bg(self._stream_off)
        if cmd == "exec_status":
            return self._bg(self._exec_status)
        if cmd == "exec_start":
            return self._bg(self._exec_start)
        if cmd == "exec_off":
            return self._bg(lambda: self._exec_post("/api/disarm", {}, "LIVE execution is OFF: no new order will be sent. An open position keeps its broker stop-loss and is still managed."))
        if cmd == "exec_live":
            self.pending = ("exec_live", now + CONFIRM_SECONDS)
            return self.say(f"ACTIVATE LIVE sends REAL orders to FYERS (1 lot per dashboard trade; a restart returns to PAPER). Send /confirm within {CONFIRM_SECONDS} seconds to activate, /cancel to stop.")
        if cmd == "exec_exit":
            self.pending = ("exec_exit", now + CONFIRM_SECONDS)
            return self.say(f"EMERGENCY EXIT cancels unfilled entries, exits the open position, switches LIVE off and writes the KILL file. Send /confirm within {CONFIRM_SECONDS} seconds, /cancel to stop.")
        if cmd in ("sleep", "shutdown"):
            self.pending = (cmd, now + CONFIRM_SECONDS)
            return self.say(f"Send /confirm within {CONFIRM_SECONDS} seconds to {'put the PC to sleep' if cmd == 'sleep' else 'shut the PC down'}. /cancel to stop.")
        if cmd == "confirm":
            if not self.pending:
                return self.say("Nothing to confirm.")
            action, self.pending = self.pending[0], None
            if action == "exec_live":
                return self._bg(lambda: self._exec_post("/api/arm", {"phrase": "ACTIVATE LIVE"}, None))
            if action == "exec_exit":
                return self._bg(lambda: self._exec_post("/api/emergency", {"phrase": "EXIT NOW"}, None))
            return self._power(action)
        if cmd == "cancel":
            self.pending = None
            subprocess.run(["shutdown", "/a"], capture_output=True)
            return self.say("Cancelled.")
        self.say("Unknown command. /help lists them.")

    def _bg(self, fn):
        threading.Thread(target=lambda: self.say(fn()), daemon=True).start()
        self.say("On it...")

    def _stream_on(self):
        import obs_control
        cfg = obs_control.load_config()
        if not cfg or not cfg.get("password"):
            return "OBS is not set up (data/obs.json)."
        try:
            obs_control.set_hold(None)
            return obs_control.golive(cfg)
        except Exception as e:
            return "Could not start: " + str(e)[:150]

    def _stream_off(self):
        import obs_control
        cfg = obs_control.load_config()
        if not cfg or not cfg.get("password"):
            return "OBS is not set up (data/obs.json)."
        try:
            obs_control.set_hold(obs_control.now_ist().strftime("%Y-%m-%d"))          # the automatic start must not bring it back today
            return obs_control.stop_stream(cfg) + ". It stays off today; /stream_on starts it again."
        except Exception as e:
            return "Could not stop: " + str(e)[:150]

    EXEC = "http://127.0.0.1:8780"

    def _exec_call(self, path, body=None):
        """The engine's own status page API (this PC only). Returns the JSON, or None when the engine is not running."""
        try:
            r = requests.request("POST" if body is not None else "GET", self.EXEC + path, json=body, headers={"X-Requested-With": "execution-ui"}, timeout=20)
            return r.json()
        except Exception:
            return None

    def _exec_post(self, path, body, ok_text):
        j = self._exec_call(path, body)
        if j is None:
            return "The execution engine is not running (or not answering). /exec_start starts it in PAPER."
        msg = j.get("msg") or ""
        return (ok_text if (ok_text and j.get("ok")) else msg) or str(j)[:200]

    def _exec_start(self):
        try:
            requests.post("http://127.0.0.1:5050/exec/start", headers={"X-BP": "1"}, timeout=20)
        except Exception:
            return "The helper is not answering, so I cannot start the engine. Open Refresh BharatPulse.bat on the PC."
        for _ in range(10):
            time.sleep(2)
            if self._exec_call("/api/status") is not None:
                return "The execution engine is running in PAPER. /exec_live activates real orders."
        return "I asked for the engine to start but it is not answering yet; try /exec_status in a minute."

    def _exec_status(self):
        s = self._exec_call("/api/status")
        if s is None:
            return "The execution engine is NOT running. /exec_start starts it in PAPER."
        c, inr = s.get("current"), (lambda x: "n/a" if x is None else f"{'+' if x >= 0 else '-'}Rs {abs(round(x)):,}")
        lines = [f"Mode: {s.get('mode')}" + (f" ({s.get('note')})" if s.get("note") and s.get("mode") != "LIVE" else ""),
                 f"Sign-in: {'OK' if s.get('auth', {}).get('ok') else 'NEEDED (order sign-in)'} | FYERS: {'OK' if s.get('connection', {}).get('ok') else 'PROBLEM'} | dashboard feed: {'fresh' if s.get('dashboard', {}).get('fresh') else 'STALE'}",
                 f"Halted: {s.get('halted') or 'no'} | entries today: {s.get('entries_today')} | realised {inr(s.get('realized'))} | open {inr(s.get('unrealized'))}"]
        if c:
            lines.append(f"Trade: {c.get('symbol')} {c.get('phase')} qty {c.get('qty') or '-'} entry {c.get('avg_entry')} stop {c.get('sl_stop')}")
        else:
            lines.append("No open trade.")
        return chr(10).join(lines)

    def _power(self, action):
        if action == "sleep":
            self.say("Going to sleep in 5 seconds. Everything stops until you wake the PC.")
            threading.Timer(5, lambda: subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])).start()
        else:
            self.say("Shutting down in 30 seconds. /cancel stops it.")
            subprocess.Popen(["shutdown", "/s", "/t", "30", "/c", "BharatPulse: shutdown asked from Telegram"])


def run(status_fn, stop=None, log=lambda *a: None):
    """Long-poll the bot until stop is set. Safe to run in a thread; two copies at once just fight over the updates, so start it once."""
    stop = stop or threading.Event()
    token, chat = telegram_notify.config()
    if not token:
        return
    ctl = Control(status_fn, chat, lambda text: telegram_notify.send(text))
    base = f"https://api.telegram.org/bot{token}/"
    offset = None
    try:                                                       # skip everything that was sent before now; show the command menu only in your own chat
        r = requests.get(base + "getUpdates", params={"timeout": 0}, timeout=15).json()
        if r.get("ok") and r.get("result"):
            offset = r["result"][-1]["update_id"] + 1
        requests.post(base + "setMyCommands", json={"commands": [{"command": c, "description": d} for c, d in COMMANDS],
                                                     "scope": {"type": "chat", "chat_id": int(chat)}}, timeout=15)
    except Exception as e:
        log("tg_control start:", type(e).__name__)
    while not stop.is_set():
        try:
            r = requests.get(base + "getUpdates", params={"timeout": 25, "offset": offset, "allowed_updates": json.dumps(["message"])}, timeout=40).json()
            if not r.get("ok"):
                stop.wait(15)
                continue
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                cmd = parse(u, chat, now=time.time())
                if cmd:
                    log("tg command:", cmd)
                    ctl.handle(cmd)
        except Exception as e:
            log("tg_control:", type(e).__name__)
            stop.wait(10)


def start_in_thread(status_fn, stop=None):
    t = threading.Thread(target=run, args=(status_fn, stop), daemon=True)
    t.start()
    return t
