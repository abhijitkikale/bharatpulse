"""TradingView connection health: what the hidden TradingView browser (tools/tv_live_paste.py) found, kept in data/tv_status.json for the Telegram message, the helper
page and the stream page's chip.

A status is a list of lines {"key", "ok", "text"}: ok True = fine, False = a problem to fix, None = information only. The keys: signin (the hidden browser is signed in
to TradingView), alert (a BP Live alert exists), alert_log (TradingView's alert log can be read: this is how setups and opening candles reach the page), eod (the published
end-of-day levels are from the last trading day: an old text silences the alert), then during the session renewal (today's alert made), session (the chart session is not
taken by another browser) and feed (the alert log was read a moment ago). It also holds the day's signal-source override: when the morning check finds the TradingView feed
broken, the page books from its own setups for that day (data/signal_source_day.json), so a broken feed never means a silent day. Pure helpers around two small files."""
import json
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATUS = ROOT / "data" / "tv_status.json"
OVERRIDE = ROOT / "data" / "signal_source_day.json"
REQUIRED = ("signin", "alert_log", "eod")
HINTS = {
    "signin": "The hidden browser is signed OUT of TradingView. On the PC double-click 'TradingView Sign-in.bat', sign in, close the window, then restart the helper.",
    "alert_log": "TradingView's alert log could not be read, so setups and opening candles cannot reach the page.",
    "eod": "The published dashboard is older than the last trading day's close, so the alert would carry old levels and stay silent. Start the evening build: run 'gh workflow run refresh.yml' (it takes about 5 minutes).",
    "renewal": "Today's BP Live alert has not been made. Without it TradingView sends no setups.",
    "session": "Another TradingView session took the hidden browser's chart session. Close TradingView on your other devices (the alert itself keeps working).",
    "feed": "The alert-log reader has stopped: no new TradingView messages are reaching the page.",
}


def _now_text(now):
    return now.strftime("%H:%M")


def overall(lines):
    """True when no line is a problem (information lines do not count)."""
    return all(l.get("ok") is not False for l in lines)


def _write(d, path=None):
    p = pathlib.Path(path or STATUS)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, separators=(",", ":")), encoding="utf-8")


def read(path=None, now=None):
    """The saved status ({} when there is none) with 'age_s' = seconds since it was last written."""
    try:
        d = json.loads(pathlib.Path(path or STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if now is not None and d.get("checked_at"):
        d["age_s"] = int((now - pd.Timestamp(d["checked_at"])).total_seconds())
    return d


def write(lines, now, phase="check", path=None):
    """Replace the whole status (a fresh check)."""
    d = {"checked_at": now.isoformat(timespec="seconds"), "phase": phase, "ok": overall(lines), "lines": lines}
    _write(d, path)
    return d


def update(key, ok, text, now, path=None):
    """Change one line (or add it) and recompute the overall flag."""
    d = read(path) or {"lines": [], "phase": "session"}
    lines = [l for l in d.get("lines", []) if l["key"] != key] + [{"key": key, "ok": ok, "text": text}]
    d.update(lines=lines, ok=overall(lines), checked_at=now.isoformat(timespec="seconds"))
    _write(d, path)
    return d


def format_message(status, title):
    """The Telegram text: every line with a tick, cross or dot, and for each problem what to do."""
    lines = status.get("lines", [])
    bad = [l for l in lines if l.get("ok") is False]
    out = [f"{title}: " + ("ALL OK" if not bad else "PROBLEM, fix before you leave")]
    for l in lines:
        out.append(("✓ " if l.get("ok") else "✗ " if l.get("ok") is False else "• ") + l["text"])
    for l in bad:
        if l["key"] in HINTS:
            out.append("→ " + HINTS[l["key"]])
    return "\n".join(out)


def set_override(day, source, why, path=None):
    p = pathlib.Path(path or OVERRIDE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"day": str(day), "source": source, "why": why}), encoding="utf-8")


def source_override(day, path=None):
    """'fyers' when today's override says the page must book from its own setups, else None. An override from another day is ignored."""
    try:
        d = json.loads(pathlib.Path(path or OVERRIDE).read_text(encoding="utf-8"))
        return d.get("source") if d.get("day") == str(day) else None
    except (OSError, ValueError):
        return None


SILENT_AFTER_S = 300                 # during the session TradingView sends one message per minute; none for 5 minutes = the feed is dead
WATCH_FROM, WATCH_TO = (9, 20), (15, 25)


def feed_silent(last_new_s, now_s, hm, limit=SILENT_AFTER_S):
    """True when no new TradingView message arrived for `limit` seconds inside the watched part of the session. last_new_s / now_s: epoch seconds."""
    return WATCH_FROM <= tuple(hm) < WATCH_TO and (now_s - last_new_s) > limit


def midday_fallback(now, silent_since, why, override_path=None, pause_path=None):
    """The feed went silent during the session: book from the page's own setups for the rest of today (same switch as the morning fallback), and mark the
    setups since `silent_since` as not taken, so the page does not replay what it never saw live as trades (three replayed stops add up to the -30% daily
    loss limit and would block every new entry)."""
    import trading_pause
    set_override(now.date(), "fyers", why, override_path)
    trading_pause.add_window(silent_since, now, pause_path)


def last_trading_day(day, sessions):
    """The trading day before `day`. sessions(date) -> list (empty on weekends and holidays), e.g. nse_calendar.sessions."""
    d = pd.Timestamp(day).date()
    for _ in range(12):
        d = d - pd.Timedelta(days=1)
        if sessions(d):
            return d
    return None
