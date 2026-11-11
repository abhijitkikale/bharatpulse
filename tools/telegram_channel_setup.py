"""One-time: connect the friends' Telegram channel.

1. In Telegram: New channel (private is fine), name it as you like. Open the channel's info > Administrators > Add administrator, search
   GEXINDICATORBOT and add it (it only needs the right to post messages). You are the owner; your friends join with the channel's invite link
   and can only read.
2. Run:  python tools/telegram_channel_setup.py
   It waits up to 3 minutes for the channel to show up (so run it, THEN add the bot as administrator, or post any message in the channel),
   saves the channel in data/telegram.json (this PC only) and sends a test message there.
Check with:  python tools/telegram_channel_setup.py --test"""
import json
import sys
import time

import requests

import telegram_notify


def main():
    token, chat = telegram_notify.config()
    if not token:
        sys.exit("Run tools/telegram_setup.py first (the bot token and your own chat).")
    if "--test" in sys.argv:
        ok, msg = telegram_notify.send_html("\U0001F535 <b>BharatPulse channel test</b>\nIf you can read this, the channel is connected.", to_channel=True)
        print("your chat + channel:", ok, msg)
        return
    base = f"https://api.telegram.org/bot{token}/"
    print("Waiting for the channel (up to 3 minutes). Add the bot as administrator of the channel now, or post a message in it...")
    end, offset, found = time.time() + 180, None, None
    while time.time() < end and not found:
        r = requests.get(base + "getUpdates", params={"timeout": 20, "offset": offset, "allowed_updates": json.dumps(["my_chat_member", "channel_post"])}, timeout=40).json()
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            c = (u.get("my_chat_member") or {}).get("chat") or (u.get("channel_post") or {}).get("chat") or {}
            if c.get("type") == "channel":
                found = c
    if not found:
        sys.exit("No channel seen. Add the bot as an administrator of the channel and run this again.")
    cfg = json.loads(telegram_notify.CFG.read_text(encoding="utf-8"))
    cfg["channel"] = found["id"]
    telegram_notify.CFG.write_text(json.dumps(cfg), encoding="utf-8")
    ok, msg = telegram_notify._post_html(token, found["id"], "\U0001F535 <b>BharatPulse channel connected</b>\nLive GEX levels and paper trades will appear here.")
    print(f"Saved channel '{found.get('title')}'. Test message: {msg}")


if __name__ == "__main__":
    main()
