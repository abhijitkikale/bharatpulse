"""One-time OBS setup for the automatic broadcast.

Before running, in OBS Studio: Tools > WebSocket Server Settings > tick "Enable WebSocket server", set a Server Password (use a strong one),
press OK. Then, in the BharatPulse folder:

  python tools/obs_setup.py

It asks for that password (shown on screen while you paste it), saves it only in data/obs.json on this PC (git-ignored), connects to OBS,
creates the scene "BharatPulse" with the stream page as a 1920x1080 Browser source, and prints what is left to do in OBS and on YouTube.
The mode is "direct" (the broadcast starts right after your Fyers sign-in) unless you give another one:
  python tools/obs_setup.py at_open      starts at 09:31 IST once the feed is healthy (the later setting)
  python tools/obs_setup.py off          the helper only opens OBS; you press Start Streaming yourself"""
import json
import sys

import obs_control


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "direct"
    if mode not in ("direct", "at_open", "off"):
        sys.exit("The mode must be direct, at_open or off.")
    cfg = obs_control.load_config() or dict(obs_control.DEFAULTS)
    pw = input("OBS WebSocket password (press Enter to keep the saved one): ").strip() or cfg.get("password", "")
    if not pw:
        sys.exit("No password. Enable the WebSocket server in OBS first (Tools > WebSocket Server Settings).")
    cfg.update(password=pw, mode=mode)
    try:
        obs = obs_control.Obs(cfg["host"], cfg["port"], pw).connect()
    except Exception as e:
        sys.exit(f"Could not connect to OBS ({type(e).__name__}: {e}). Is OBS open with the WebSocket server enabled, and is the password right?")
    try:
        obs.ensure_scene(cfg["scene"], cfg["url"])
    finally:
        obs.close()
    obs_control.CFG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    print(f"Saved. Scene '{cfg['scene']}' with the stream page is ready in OBS. Mode: {mode}.")
    print()
    print("Still to do once, by you:")
    print("  1. OBS > Settings > Stream: choose YouTube and sign in (or paste your stream key).")
    print("  2. YouTube Studio: set the default privacy to Private and share it with your friends' Google accounts (see README).")


if __name__ == "__main__":
    main()
