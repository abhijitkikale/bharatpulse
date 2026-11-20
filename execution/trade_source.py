"""Reads the dashboard's trade objects. The dashboard (tools/stream_server.py) writes its live state to data/stream_state.json on every cycle; the "trades" list in it is the
same trade object the paper trades, the journal and Telegram use. This module only READS that file: it never writes to it or to any other dashboard file.

Trade object fields used (see pipeline/stream_trades.py to_json): n, side (CALL/PUT), strike, symbol (the Fyers contract), entry (the entry price the dashboard assumed),
stop (initial) and stop_now (in force now, trailing / regime moves included), target, status (OPEN / TARGET / STOP / TRAIL / DAY END), exit, last, entry_time, setup_time,
tp_pct, sl_pct. Nothing is computed or defaulted here: a trade missing a required value is reported and not executed."""
import json
import time

from . import config
from .models import parse_intent


def read(day, path=None, max_age_s=None):
    """(intents, fresh, problems). fresh=False when the file is missing, older than max_age_s, or is for another day (then nothing new may be entered)."""
    path = path or config.DASH_STATE
    max_age_s = max_age_s if max_age_s is not None else config.DEFAULTS["stale_state_s"]
    try:
        age = time.time() - path.stat().st_mtime
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False, ["the dashboard state file cannot be read"]
    if str(state.get("date")) != str(day):
        return [], False, [f"the dashboard state is for {state.get('date')}, not {day}"]
    fresh = age <= max_age_s
    intents, problems = [], []
    for t in (state.get("trades") or {}).get("trades") or []:
        it, bad = parse_intent(day, t)
        if it is None:
            problems.append(f"trade {t.get('n')}: " + "; ".join(bad))
        else:
            intents.append(it)
    if not fresh:
        problems.append(f"the dashboard state is {age:.0f} s old")
    return intents, fresh, problems
