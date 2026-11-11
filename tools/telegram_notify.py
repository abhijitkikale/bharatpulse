"""Send the live level text to your own Telegram chat (bot GEXINDICATORBOT).

The bot token and your chat id live only on this PC, in data/telegram.json (git-ignored, never published). Create that file once with
  python tools/telegram_setup.py
Environment variables BHARATPULSE_TG_TOKEN / BHARATPULSE_TG_CHAT override the file. Nothing is sent when neither is set."""
import html
import json
import os
import pathlib

import requests

CFG = pathlib.Path(__file__).resolve().parent.parent / "data" / "telegram.json"


def config():
    """(token, chat_id) or (None, None) when Telegram is not set up."""
    token, chat = os.environ.get("BHARATPULSE_TG_TOKEN"), os.environ.get("BHARATPULSE_TG_CHAT")
    if not (token and chat) and CFG.exists():
        try:
            j = json.loads(CFG.read_text(encoding="utf-8"))
            token, chat = token or j.get("token"), chat or j.get("chat_id")
        except Exception:
            pass
    return (token, str(chat)) if token and chat else (None, None)


def send(text):
    """Send `text` as a copyable (monospace) message. Returns (ok, short message). The token is never part of the message text."""
    token, chat = config()
    if not token:
        return False, "Telegram not set up"
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                          json={"chat_id": chat, "text": "<pre>" + html.escape(text) + "</pre>", "parse_mode": "HTML", "disable_web_page_preview": True})
        if r.status_code == 200 and r.json().get("ok"):
            return True, "sent"
        return False, "Telegram refused: " + str(r.json().get("description", r.status_code))[:100]
    except Exception as e:
        return False, "Telegram failed: " + type(e).__name__


def send_video(path, caption=""):
    """Send a video file to your own chat (Telegram allows up to 50 MB for bots). Returns (ok, short message)."""
    token, chat = config()
    if not token:
        return False, "Telegram not set up"
    try:
        with open(path, "rb") as f:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendVideo", timeout=180,
                              data={"chat_id": chat, "caption": caption[:1000], "supports_streaming": "true"}, files={"video": f})
        if r.status_code == 200 and r.json().get("ok"):
            return True, "sent"
        return False, "Telegram refused: " + str(r.json().get("description", r.status_code))[:100]
    except Exception as e:
        return False, "Telegram failed: " + type(e).__name__


def channel():
    """The friends' channel (@name or -100... id) from data/telegram.json ("channel") or BHARATPULSE_TG_CHANNEL, or None. Create it with tools/telegram_channel_setup.py."""
    c = os.environ.get("BHARATPULSE_TG_CHANNEL")
    if not c and CFG.exists():
        try:
            c = json.loads(CFG.read_text(encoding="utf-8")).get("channel")
        except Exception:
            c = None
    return str(c) if c else None


def _post_html(token, chat, text):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=15,
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    if r.status_code == 200 and r.json().get("ok"):
        return True, "sent"
    return False, "Telegram refused: " + str(r.json().get("description", r.status_code))[:100]


def send_html(text, to_channel=False):
    """A formatted (HTML) message to your own chat and, when to_channel is set and a channel is configured, to the friends' channel too.
    Returns (ok, short message); ok means your own chat got it."""
    token, chat = config()
    if not token:
        return False, "Telegram not set up"
    try:
        ok, msg = _post_html(token, chat, text)
        ch = channel() if to_channel else None
        if ch:
            ok2, msg2 = _post_html(token, ch, text)
            msg += " | channel: " + msg2
        return ok, msg
    except Exception as e:
        return False, "Telegram failed: " + type(e).__name__


def send_alert(text):
    """A trade message from the stream page: coloured and formatted; the friends' channel gets the public kinds (trade taken, target, stop-loss, closed),
    your own chat gets all of them (setups not taken stay with you). Messages of an unknown kind go to your chat as plain text."""
    import tg_style
    kind, body = tg_style.to_html(text)
    if body is None:
        return send(text)
    return send_html(body, to_channel=tg_style.is_public(kind))


def send_channel_gex(name, block, now):
    """The live GEX levels as a blue message for the friends' channel only (your own chat already gets the copyable text)."""
    import tg_style
    ch = channel()
    token, _ = config()
    if not (ch and token):
        return False, "no channel"
    try:
        return _post_html(token, ch, tg_style.gex_html(name, block, now))
    except Exception as e:
        return False, "Telegram failed: " + type(e).__name__


def _logged(fn):
    """Every send attempt is added to data/telegram_log.txt (time, result, first words), so a missing message can be traced. The token is never logged."""
    import datetime
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **k):
        r = fn(*a, **k)
        try:
            first = str(a[0] if a else "").replace("\n", " ")[:70]
            with open(CFG.parent / "telegram_log.txt", "a", encoding="utf-8") as f:
                f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} PC clock | {fn.__name__} | {'ok' if r[0] else 'FAILED'} {r[1]} | {first}\n")
        except Exception:
            pass
        return r
    return wrapper


send = _logged(send)
send_video = _logged(send_video)
send_html = _logged(send_html)
send_alert = _logged(send_alert)
send_channel_gex = _logged(send_channel_gex)
