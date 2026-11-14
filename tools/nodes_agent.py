"""Nodes agent: every minute in market hours it reads the Nifty option chain from Fyers, works out the nodes (see pipeline/nodes.py and
docs/superpowers/specs/2026-11-14-nodes-agent-design.md) and writes data/nodes/latest.json for the live stream page (it serves /nodes.json).

It never touches trades, Telegram or the stream state: it only READS data/stream_state.json (the page's levels and setups) to grade setups.
Normally the helper (tools/refresh_app.py) starts it with the live updates. By hand:
  python tools/nodes_agent.py            run until closed (waits outside market hours)
  python tools/nodes_agent.py --once     one snapshot now (works on a closed day with the last chain), print a summary, exit
The 5-minute-or-so buffer behind the velocity arrows is saved to data/nodes/YYYY-MM-DD.jsonl (kept for the end-of-day page and later calibration)."""
import argparse
import collections
import json
import os
import pathlib
import sys
import time

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import fetch_fyers  # noqa: E402
import nodes  # noqa: E402
import nse_calendar  # noqa: E402

DIR = ROOT / "data" / "nodes"
LATEST = DIR / "latest.json"
STATE = ROOT / "data" / "stream_state.json"
SYM = "NIFTY"
CYCLE = 60            # seconds between snapshots
SAVE_EVERY = 120      # seconds between lines of the saved buffer


def now_ist():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def lock_or_exit():
    """One agent only (the helper may be restarted while an old agent still runs). Returns the open lock file; exits if another agent holds it."""
    DIR.mkdir(parents=True, exist_ok=True)
    f = open(DIR / "agent.lock", "w")
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another nodes agent is already running")
        sys.exit(0)
    return f


def write_atomic(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def read_state():
    """The page's state (levels, setups) when it is recent, else None."""
    try:
        if time.time() - STATE.stat().st_mtime > 600:
            return None
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return None


def today_candles():
    """(today's 1-minute candles, the previous session's last close) or (None, None)."""
    frames, _ = fetch_fyers.fetch_intraday(days=3, symbols=(SYM,))
    df = frames.get(SYM)
    if df is None or df.empty:
        return None, None
    day = df.index.max().date()
    prev = df[df.index.date < day]
    return df[df.index.date == day], (float(prev["c"].iloc[-1]) if len(prev) else None)


def load_hist(day):
    """Today's saved buffer -> deque of (Timestamp, {strike: Front value}) so the velocity arrows survive a restart."""
    h = collections.deque(maxlen=400)
    try:
        for line in (DIR / f"{day}.jsonl").read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            h.append((pd.Timestamp(r["t"]), {k: v for k, v in zip(r["k"], r["n"])}))
    except Exception:
        pass
    return h


def setups_of(state):
    """The page's setups as [{"when": "HH:MM", "side", "level"}] (trades.signals when the trades are on, else the setups table)."""
    out = []
    if not state:
        return out, None
    day = state.get("date")
    sig = ((state.get("trades") or {}).get("signals")) or []
    for s in sig:
        out.append({"when": s["when"], "side": s["side"], "level": s["level"]})
    if not out:
        for r in (state.get("setups") or {}).get("table", []):
            out.append({"when": r["when"][-5:], "side": r["side"], "level": r["level"]})
    return out, day


def grade_all(snap, state, candles, now):
    """Grade every setup of the day once (the first grade is kept); returns the list for the snapshot."""
    setups, day = setups_of(state)
    if not setups or not day:
        return []
    gf = DIR / f"grades_{day}.json"
    try:
        kept = json.loads(gf.read_text(encoding="utf-8"))
    except Exception:
        kept = {}
    changed = False
    for s in setups:
        key = nodes.setup_key(day, s["when"], s["side"], s["level"])
        if key not in kept:
            g = nodes.grade_setup(s, snap, state.get("levels"), candles, day, now)
            if g:
                kept[key] = {"when": s["when"], "side": s["side"], "level": s["level"], **g}
                changed = True
    if changed:
        write_atomic(gf, kept)
    return list(kept.values())


def cycle(hist, last_save, force_save=False):
    now = now_ist()
    chains, status = fetch_fyers.fetch_chains(strikes=40, symbols=(SYM,), max_expiries=nodes.N_EXP)
    if SYM not in chains:
        return None, last_save, status
    candles, prev_close = today_candles()
    snap = nodes.build_snapshot(chains[SYM], now, candles, hist, prev_close)
    if snap is None:
        return None, last_save, "no usable chain"
    snap["grades"] = grade_all(snap, read_state(), candles, now)
    snap["status"] = "ok"
    write_atomic(LATEST, snap)
    front = snap["columns"][0]
    hist.append((now, {r["k"]: r["net"] for r in front["strikes"]}))
    if force_save or time.time() - last_save >= SAVE_EVERY:
        try:
            with open(DIR / f"{now.date()}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": now.isoformat(timespec="seconds"), "spot": snap["spot"], "k": [r["k"] for r in front["strikes"]],
                                    "n": [r["net"] for r in front["strikes"]], "flip": front["flip"], "total_cr": front["total_cr"]}, separators=(",", ":")) + "\n")
            last_save = time.time()
        except OSError:
            pass
    return snap, last_save, "ok"


def summary(snap):
    s = snap["structure"]
    print(f"{snap['asof']}  spot {snap['spot']}  front {snap['front']}  regime {snap['regime']['now']} (flip {snap['regime']['flip']})  expected move {snap['exp_move']}")
    if snap.get("expected"):
        e = snap["expected"]
        print(f"expected move (1 sigma {e['sigma']} from {e['ref']}): {e['dn1']} - {e['up1']}   used so far: {e['pace']:+.2f} sigma")
    print(f"King {s.get('king')}  gatekeeper {s.get('gatekeeper')}  floor {s.get('floor')}  ceiling {s.get('ceiling')}  midpoint {s.get('midpoint')}  air pockets {s.get('air_pockets')}")
    for n in snap["nodes"][:8]:
        print(f"  {n['k']:.0f} {n['sign']} {n['pct']:5.1f}%  {','.join(n['roles']) or '-':22} touches {n['touches']}  {','.join(n['flags'])}")
    for g in snap.get("grades", [])[-5:]:
        print(f"  setup {g['when']} {g['side']} {g['level']}: grade {g['grade']} ({g['score']:+d}) {'; '.join(g['reasons'])}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.once:
        snap, _, st = cycle(collections.deque(maxlen=400), 0.0, force_save=False)
        print(st if snap is None else "")
        if snap:
            summary(snap)
        return
    lock = lock_or_exit()                                        # noqa: F841  (kept open for the life of the process)
    hist, last_save, day = collections.deque(maxlen=400), 0.0, None
    while True:
        t = now_ist()
        try:
            if nse_calendar.in_session(t, lead=5, tail=10):
                if day != t.date():
                    day, hist, last_save = t.date(), load_hist(t.date()), 0.0
                snap, last_save, msg = cycle(hist, last_save)
                if snap is None:
                    print(time.strftime("%H:%M:%S"), "nodes agent:", msg, flush=True)
        except Exception as e:                                   # never die: log and try again next minute
            print(time.strftime("%H:%M:%S"), "nodes agent error:", str(e)[:200], flush=True)
        time.sleep(CYCLE)


if __name__ == "__main__":
    main()
