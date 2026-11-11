"""Telegram texts for the live stream page: one message when a setup is taken as a paper trade, one when it closes, one line for setups that are ignored.
Pure function (no network): new_messages() compares the day's run_trades() result with what was already sent and returns only what is new.
Facts only (side, strike, level, prices, percentages); a paper-trade tracker, not advice."""
import re

import pandas as pd


def _hm(t):
    return None if t is None else t.strftime("%H:%M")


def _opt(side):
    return "CE" if side == "CALL" else "PE"


def new_messages(res, sent, now, fresh_minutes=3):
    """res: stream_trades.run_trades() result. sent: set of keys already sent (updated here). now: tz-aware pandas Timestamp (IST).
    Events older than `fresh_minutes` are marked as sent without a message (a restart in mid-session or a replayed day must not flood the chat).
    Returns [text, ...] in time order."""
    out = []

    def fresh(t):
        return t is not None and (now - t) <= pd.Timedelta(minutes=fresh_minutes)

    def add(key, t, text):
        if key in sent:
            return
        sent.add(key)
        if fresh(t):
            out.append((t, text))

    for tr in res.get("trades", []):
        n = tr["n"]
        add(f"T{n}open", tr["entry_time"],
            f"TRADE {n} (paper): {tr['side']} {tr['strike']} {_opt(tr['side'])} on {tr['level']}\nentry {_hm(tr['entry_time'])} at {tr['entry']:.2f}  "
            f"target {tr['target']:.2f} (+40%)  stop {tr['stop']:.2f} (-10%)")
        if tr.get("exit_time") is not None:
            add(f"T{n}close", tr["exit_time"],
                f"TRADE {n} closed: {tr['status']} {tr['pnl_pct']:+.1f}%  ({tr['side']} {tr['strike']} {_opt(tr['side'])}, out {_hm(tr['exit_time'])} at {tr['exit']:.2f})")
    for s in res.get("signals", []):
        if s["status"].startswith("TRADE"):
            continue
        # the key leaves out every number of the level text: the VWAP value drifts a few paise each minute and would send the same setup again and again
        add(f"S{_hm(s['time'])}{s['side']}{re.sub(r'[0-9.]+', '', s['level'])}", s["time"],
            f"Setup not taken ({s['status']}): {s['side']} {s['strike']} {_opt(s['side'])} on {s['level']} at {_hm(s['time'])}")
    out.sort(key=lambda x: x[0])
    return [t for _, t in out]
