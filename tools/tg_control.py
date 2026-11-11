"""Control BharatPulse from your phone: commands to the Telegram bot (GEXINDICATORBOT), answered only for YOU.

  /status       what is running: Fyers sign-in, live updates, stream page, YouTube broadcast, today's trades
  /stream_on    start the YouTube broadcast now          /stream_off   stop it (it stays off for the rest of the day)
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

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pipeline"))
import telegram_notify  # noqa: E402

COMMANDS = [("status", "What is running"), ("stream_on", "Start the YouTube broadcast"), ("stream_off", "Stop the YouTube broadcast"),
            ("sleep", "Put the PC to sleep"), ("shutdown", "Shut the PC down"), ("confirm", "Confirm sleep or shutdown"), ("cancel", "Cancel"), ("help", "List the commands")]
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
        if cmd == "stream_on":
            return self._bg(self._stream_on)
        if cmd == "stream_off":
            return self._bg(self._stream_off)
        if cmd in ("sleep", "shutdown"):
            self.pending = (cmd, now + CONFIRM_SECONDS)
            return self.say(f"Send /confirm within {CONFIRM_SECONDS} seconds to {'put the PC to sleep' if cmd == 'sleep' else 'shut the PC down'}. /cancel to stop.")
        if cmd == "confirm":
            if not self.pending:
                return self.say("Nothing to confirm.")
            action, self.pending = self.pending[0], None
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
