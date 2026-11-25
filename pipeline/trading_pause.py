"""Pause new entry signals from your phone (Telegram /pause and /resume, see tools/tg_control.py).

While paused, a setup that appears is listed as "not taken: paused (you sent /pause)" and tested like any other skipped setup; a trade that is already open
carries on (its stop, target and trailing moves still work). The pauses are kept as time intervals in data/trading_pause.json, so the page's replay of the day
treats each setup by the time it appeared, and a pause never carries over to the next day. Pure helpers plus a tiny file."""
import json
import pathlib

import pandas as pd

FILE = pathlib.Path(__file__).resolve().parent.parent / "data" / "trading_pause.json"


def _load(path=None):
    try:
        return json.loads(pathlib.Path(path or FILE).read_text(encoding="utf-8")).get("intervals", [])
    except (OSError, ValueError):
        return []


def _save(iv, path=None):
    p = pathlib.Path(path or FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"intervals": iv}), encoding="utf-8")


def is_paused_at(ts, intervals):
    """True when `ts` falls inside any [start, end) of the intervals (end None = still paused); only the same calendar day counts."""
    for s, e in intervals:
        start = pd.Timestamp(s)
        end = pd.Timestamp(e) if e else None
        if start.date() == ts.date() and ts >= start and (end is None or ts < end):
            return True
    return False


def paused_fn(path=None):
    """paused_at(time) for run_trades, reading the file now."""
    iv = _load(path)
    return lambda ts: is_paused_at(ts, iv)


def pause(now, path=None):
    """Start a pause now (does nothing if one is already open). Returns True when a new pause started."""
    iv = _load(path)
    if iv and iv[-1][1] is None and pd.Timestamp(iv[-1][0]).date() == now.date():
        return False
    iv.append([now.isoformat(timespec="seconds"), None])
    _save(iv, path)
    return True


def add_window(start, end, path=None):
    """Mark a closed window [start, end) as paused: setups inside it are listed as not taken (used when the TradingView feed went silent and the page
    switched to its own setups, so the setups the page never saw live are not replayed as trades)."""
    iv = _load(path)
    iv.append([pd.Timestamp(start).isoformat(timespec="seconds"), pd.Timestamp(end).isoformat(timespec="seconds")])
    _save(iv, path)


def resume(now, path=None):
    """End the open pause. Returns True when there was one."""
    iv = _load(path)
    if iv and iv[-1][1] is None:
        iv[-1][1] = now.isoformat(timespec="seconds")
        _save(iv, path)
        return True
    return False


def is_paused_now(now, path=None):
    return is_paused_at(now, _load(path))
