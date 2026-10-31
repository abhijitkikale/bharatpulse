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
