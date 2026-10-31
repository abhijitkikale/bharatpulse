"""One-time Telegram setup for the live levels (bot GEXINDICATORBOT).

Before running: open https://t.me/GEXINDICATORBOT in Telegram and press Start (or send "hi"). Then run
  python tools/telegram_setup.py
and paste the bot token (from @BotFather) when asked (right-click pastes in a Windows terminal; the token is visible while pasted,
or give it as an argument: python tools/telegram_setup.py <token>). The token and your chat id are saved only to
data/telegram.json on this PC (git-ignored). A test message is sent to your chat."""
import json
import sys

import requests

import telegram_notify


def main():
    token = (sys.argv[1] if len(sys.argv) > 1 else input("Paste the bot token from @BotFather and press Enter: ")).strip()
    if not token:
        sys.exit("No token entered.")
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
    except Exception as e:
        sys.exit("Could not reach Telegram: " + type(e).__name__)
    if not r.get("ok"):
        sys.exit("Telegram did not accept that token. Check it and try again.")
    chats = {}
    for u in r.get("result", []):
        m = u.get("message") or u.get("edited_message") or {}
        c = m.get("chat") or {}
        if c.get("type") == "private" and c.get("id"):
            chats[c["id"]] = c.get("first_name") or ""
    if not chats:
        sys.exit("No message to the bot found. Open https://t.me/GEXINDICATORBOT, press Start, then run this again.")
    chat_id, name = list(chats.items())[-1]
    telegram_notify.CFG.parent.mkdir(parents=True, exist_ok=True)
    telegram_notify.CFG.write_text(json.dumps({"token": token, "chat_id": chat_id}), encoding="utf-8")
    ok, msg = telegram_notify.send("BharatPulse live levels: Telegram is set up. You will get a message here when a live level moves by one strike.")
    print(f"Saved for the chat with {name or chat_id}. Test message: {msg}")


if __name__ == "__main__":
    main()
