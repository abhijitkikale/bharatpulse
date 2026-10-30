"""BharatPulse live loop - runs on YOUR PC, started by 'Start Live.bat'.

Every 30 minutes during the NSE session (09:15-15:30 IST, weekdays) it pulls today's 1-minute index candles from Fyers (your
data-only app), recomputes the developing volume profile (POC / VAH / VAL, 5-session weekly levels, VWAP), and publishes one small
file, live.json, to the 'live-data' branch of the repo. The dashboard reads it and shows it with a green "Live" banner.

It needs today's Fyers token: sign in once each morning with 'Refresh BharatPulse.bat' (the page also works for this; the build it
starts is harmless). The token is only read from data/fyers_token.json and is never printed or published.

  python tools/live_loop.py            loop until the close (runs one cycle immediately if the market is open)
  python tools/live_loop.py --once     one cycle now, then exit
  python tools/live_loop.py --no-push  write data/live.json only (nothing goes to GitHub)
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import fetch_fyers  # noqa: E402
import live  # noqa: E402

OUT = ROOT / "data" / "live.json"
REPO = ROOT / "data" / "_live_repo"      # a tiny separate clone; the branch holds one commit that is replaced each time
BRANCH = "live-data"
OPEN, CLOSE, FINAL = (9, 15), (15, 30), (15, 40)     # IST; one last cycle after the close picks up the final candles


def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def git(*args, cwd=REPO, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=120)
    if check and r.returncode:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.strip()[:200]}")
    return r


def publish(payload_text):
    if not (REPO / ".git").exists():
        REPO.mkdir(parents=True, exist_ok=True)
        url = git("remote", "get-url", "origin", cwd=ROOT).stdout.strip()
        git("init", "-q")
        git("checkout", "-q", "-b", BRANCH)
        git("remote", "add", "origin", url)
        for k in ("user.name", "user.email"):
            v = git("config", k, cwd=ROOT, check=False).stdout.strip()
            if v:
                git("config", k, v)
    (REPO / "live.json").write_text(payload_text, encoding="utf-8")
    git("add", "live.json")
    msg = "live " + now_ist().strftime("%Y-%m-%d %H:%M IST")
    if git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0:
        git("commit", "-q", "--amend", "--allow-empty", "-m", msg)          # keep ONE commit so the branch never grows
    else:
        git("commit", "-q", "-m", msg)
    git("push", "-q", "-f", "origin", BRANCH)


def cycle(push):
    frames, status = fetch_fyers.fetch_intraday(days=10)
    if not frames:
        print("  Fyers:", status)
        return False
    payload = live.build_live(frames, now_ist())
    if payload is None:
        print("  no candles for today's session yet (holiday, before the open, or an old token). Fyers says:", status)
        return False
    text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    OUT.write_text(text, encoding="utf-8")
    for sym, b in payload["symbols"].items():
        v = b["vp"]["1"]
        print(f"  {sym}: last candle {b['last']}  spot {b['spot']}  POC {v['poc']}  VAH {v['vah']}  VAL {v['val']}")
    if push:
        publish(text)
        print("  published to branch", BRANCH)
    return True


def next_slot(t):
    """Next :15 or :45 past the hour strictly after t."""
    s = t.floor("min")
    while True:
        s += pd.Timedelta(minutes=1)
        if s.minute in (15, 45):
            return s


def hm(t):
    return (t.hour, t.minute)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    push = not a.no_push
    if sys.platform == "win32":
        try:                                    # keep the PC awake while this runs (the screen may still turn off)
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        except Exception:
            pass
    if a.once:
        print(now_ist().strftime("%H:%M"), "one cycle")
        sys.exit(0 if cycle(push) else 1)
    print("Live loop started. Updates every 30 minutes (:15 and :45) until the close. Close this window to stop.")
    t = now_ist()
    if t.weekday() < 5 and OPEN <= hm(t) <= FINAL:
        print(t.strftime("%H:%M"), "market is open: running a cycle now")
        try:
            cycle(push)
        except Exception as e:
            print("  cycle failed:", str(e)[:200])
    while True:
        t = now_ist()
        if t.weekday() >= 5 or hm(t) >= FINAL:
            print(t.strftime("%H:%M"), "done for today (market closed). The evening build takes over.")
            return
        s = next_slot(t)
        if hm(s) < OPEN:
            s = s.normalize() + pd.Timedelta(hours=9, minutes=45)
        wait = (s - now_ist()).total_seconds()
        if wait > 0:
            time.sleep(wait)
        t = now_ist()
        print(t.strftime("%H:%M"), "cycle")
        try:
            cycle(push)
        except Exception as e:                  # keep the last good file and try again at the next slot
            print("  cycle failed:", str(e)[:200])
        if hm(t) >= CLOSE:
            print(t.strftime("%H:%M"), "final cycle done.")
            return


if __name__ == "__main__":
    main()
