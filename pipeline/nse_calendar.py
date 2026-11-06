"""NSE trading calendar for the stream page, the OBS automation and the morning check.

Holidays and special sessions come from NSE's own holiday list (https://www.nseindia.com/api/holiday-master?type=trading, the cash market),
cached in data/nse_calendar.json and refreshed about twice a day. Special sessions (for example Muhurat trading on Diwali) are read from the list's
morning_session / evening_session fields once NSE publishes them; until then the day is shown as "timing not announced yet". You can also add or
correct a session yourself in data/nse_special_sessions.json:
  {"2026-11-08": {"open": "18:15", "close": "19:15", "name": "Muhurat trading"}}
Without any calendar the code falls back to Monday to Friday 09:15-15:30 IST."""
import datetime as dt
import json
import pathlib
import re
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "nse_calendar.json"
MANUAL = ROOT / "data" / "nse_special_sessions.json"
URL = "https://www.nseindia.com/api/holiday-master?type=trading"
PAGE = "https://www.nseindia.com/resources/exchange-communication-holidays"
REGULAR = ((9, 15), (15, 30))
_MEM = {"cal": None, "at": 0.0}


def _times(text):
    """The first two clock times in a text such as '18:15-19:15' or '06:15 PM - 07:15 PM' as ((h, m), (h, m)), or None."""
    out = []
    for h, m, ap in re.findall(r"(\d{1,2})[:.](\d{2})\s*(AM|PM|am|pm)?", text or ""):
        h, m = int(h), int(m)
        if ap and ap.upper() == "PM" and h < 12:
            h += 12
        if ap and ap.upper() == "AM" and h == 12:
            h = 0
        out.append((h, m))
    return (out[0], out[1]) if len(out) >= 2 else None


def parse_master(j):
    """NSE's holiday-master answer -> {"holidays": {date: name}, "special": {date: {open, close, name}}, "pending": {date: name}}."""
    holidays, special, pending = {}, {}, {}
    for x in (j.get("CM") or []):
        try:
            d = dt.datetime.strptime(x["tradingDate"], "%d-%b-%Y").date().isoformat()
        except (KeyError, ValueError):
            continue
        name = (x.get("description") or "").strip()
        holidays[d] = name.rstrip("*").strip()
        win = _times(" ".join(filter(None, [x.get("morning_session"), x.get("evening_session")])))
        if win:
            special[d] = {"open": "%02d:%02d" % win[0], "close": "%02d:%02d" % win[1], "name": name.rstrip("*").strip() or "Special session"}
        elif "laxmi pujan" in name.lower() or "muhurat" in name.lower() or name.endswith("*"):
            pending[d] = name.rstrip("*").strip() + " (special session, timing not announced yet)"
    return {"holidays": holidays, "special": special, "pending": pending}


def fetch():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                      "Accept": "application/json,text/plain,*/*", "Referer": PAGE})
    s.get(PAGE, timeout=20)
    r = s.get(URL, timeout=20)
    r.raise_for_status()
    return parse_master(r.json())


def _manual():
    try:
        return {k: v for k, v in json.loads(MANUAL.read_text(encoding="utf-8")).items() if isinstance(v, dict) and "open" in v and "close" in v}
    except Exception:
        return {}


def load(max_age_hours=12):
    """The calendar, from memory, the cache or NSE. Never raises: with nothing available it returns an empty calendar (weekday rule)."""
    if _MEM["cal"] is not None and time.time() - _MEM["at"] < 3600:
        return _MEM["cal"]
    cal = None
    try:
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        if time.time() - cached.get("fetched", 0) < max_age_hours * 3600:
            cal = cached
    except Exception:
        cached = None
    if cal is None:
        try:
            cal = {**fetch(), "fetched": time.time()}
            CACHE.write_text(json.dumps(cal), encoding="utf-8")
        except Exception:
            cal = cached or {"holidays": {}, "special": {}, "pending": {}}
    cal = {**cal, "special": {**cal.get("special", {}), **_manual()}}
    for d in cal["special"]:
        cal["pending"].pop(d, None)
    _MEM["cal"], _MEM["at"] = cal, time.time()
    return cal


def sessions(day, cal=None):
    """The trading sessions of a date as [((h, m), (h, m))]; empty on a holiday or weekend."""
    cal = load() if cal is None else cal
    key = day.isoformat()
    sp = (cal.get("special") or {}).get(key)
    if sp:
        a, b = sp["open"].split(":"), sp["close"].split(":")
        return [((int(a[0]), int(a[1])), (int(b[0]), int(b[1])))]
    if day.weekday() < 5 and key not in (cal.get("holidays") or {}):
        return [REGULAR]
    return []


def is_trading_day(day, cal=None):
    return bool(sessions(day, cal))


def in_session(t, lead=5, tail=10, cal=None):
    """True from `lead` minutes before the open to `tail` minutes after the close of any session of t's date (t: tz-aware pandas Timestamp, IST)."""
    mins = t.hour * 60 + t.minute
    return any(o[0] * 60 + o[1] - lead <= mins < c[0] * 60 + c[1] + tail for o, c in sessions(t.date(), cal))


def to_json():
    cal = load()
    return {"holidays": cal.get("holidays", {}), "special": cal.get("special", {}), "pending": cal.get("pending", {})}
