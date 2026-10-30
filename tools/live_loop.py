"""BharatPulse live updates: every 30 minutes during the NSE session (09:15-15:30 IST, weekdays) pull today's 1-minute index candles
from Fyers (your data-only app), recompute the developing volume profile (POC / VAH / VAL, 5-session weekly levels, VWAP) and
publish one small file, live.json, to the 'live-data' branch of the repo. The dashboard reads it and shows a green "Live" banner.

Normally you do not start this yourself: 'Refresh BharatPulse.bat' (tools/refresh_app.py) starts it after your Fyers sign-in, or
automatically when it opens with a still-valid token. The command line works too:

  python tools/live_loop.py            loop until the close (runs one cycle immediately if the market is open)
  python tools/live_loop.py --once     one cycle now, then exit
  python tools/live_loop.py --no-push  write data/live.json only (nothing goes to GitHub)

The Fyers token is only read from data/fyers_token.json and is never printed or published."""
import argparse
import json
import pathlib
import subprocess
import sys
import threading
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
FIRST = (9, 45)                                      # first update of the day (30 minutes after the open)

STATE = {"running": False, "msg": "not started", "last": None, "next": None}     # read by the refresh page
_LOCK = threading.Lock()


def set_state(**kw):
    with _LOCK:
        STATE.update(kw)


def get_state():
    with _LOCK:
        return dict(STATE)


def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def hm(t):
    return (t.hour, t.minute)


def market_day_window(t=None):
    """True on weekdays from early morning until the last update (15:40 IST): the time when a fresh sign-in should start live updates."""
    t = t or now_ist()
    return t.weekday() < 5 and (5, 0) <= hm(t) < FINAL


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


def cycle(push, log=print):
    """One update. Returns (ok, short message for the status line)."""
    frames, status = fetch_fyers.fetch_intraday(days=10)
    if not frames:
        log("  Fyers:", status)
        return False, "Fyers did not return data: sign in again (the token may have expired)"
    payload = live.build_live(frames, now_ist())
    if payload is None:
        log("  no candles for today's session yet (holiday, before the open, or an old token). Fyers says:", status)
        return False, "no candles for today's session yet"
    text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    OUT.write_text(text, encoding="utf-8")
    for sym, b in payload["symbols"].items():
        v = b["vp"]["1"]
        log(f"  {sym}: last candle {b['last']}  spot {b['spot']}  POC {v['poc']}  VAH {v['vah']}  VAL {v['val']}")
    if push:
        publish(text)
        log("  published to branch", BRANCH)
    return True, "ok"


def next_slot(t):
    """Next :15 or :45 past the hour strictly after t, never before the first update of the day."""
    s = t.floor("min")
    while True:
        s += pd.Timedelta(minutes=1)
        if s.minute in (15, 45) and hm(s) >= FIRST:
            return s


def _cycle_safe(push, log):
    t = now_ist()
    try:
        ok, msg = cycle(push, log)
    except Exception as e:                      # keep the last good file and try again at the next slot
        ok, msg = False, "update failed: " + str(e)[:120]
        log("  cycle failed:", str(e)[:200])
    set_state(msg=("updated " if ok else "") + (t.strftime("%H:%M") + " IST" if ok else msg), last=t.strftime("%H:%M") if ok else get_state()["last"])
    return ok


def run_loop(push=True, stop=None, log=print):
    """Loop until the final update (15:40 IST) or until stop (a threading.Event) is set. Safe to call from a thread."""
    stop = stop or threading.Event()
    set_state(running=True, msg="starting")
    try:
        t = now_ist()
        if t.weekday() < 5 and OPEN <= hm(t) < FINAL:
            log(t.strftime("%H:%M"), "market is open: running a cycle now")
            _cycle_safe(push, log)
        while not stop.is_set():
            t = now_ist()
            if t.weekday() >= 5 or hm(t) >= FINAL:
                set_state(msg="finished for today; the evening build takes over", next=None)
                log(t.strftime("%H:%M"), "done for today (market closed).")
                return
            s = next_slot(t)
            set_state(next=s.strftime("%H:%M"))
            if get_state()["last"] is None:
                set_state(msg=f"waiting for the first update at {s.strftime('%H:%M')} IST")
            if stop.wait(max((s - now_ist()).total_seconds(), 0)):
                break
            t = now_ist()
            log(t.strftime("%H:%M"), "cycle")
            _cycle_safe(push, log)
            if hm(t) >= CLOSE:
                set_state(msg="final update done; the evening build takes over", next=None)
                log(t.strftime("%H:%M"), "final cycle done.")
                return
    finally:
        set_state(running=False)


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
        sys.exit(0 if cycle(push)[0] else 1)
    print("Live updates started: every 30 minutes (:15 and :45) until the close. Close this window to stop.")
    try:
        run_loop(push)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
