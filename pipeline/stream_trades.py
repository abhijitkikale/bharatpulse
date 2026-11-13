"""Paper trade tracker for the stream page: one trade at a time, taken from the setups of stream_setups.find_setups.

The first setup becomes a trade (CALL = buy the at-the-money call, PUT = buy the at-the-money put of the nearest expiry). Its entry price is the
option's 1-minute close on the setup candle. The trade stays open until +TARGET (profit) or the stop is touched. The stop starts at -STOP and trails up:
at +20% it moves to +5%, at +30% to +15%, at +35% to +30% (TRAIL, see below; a stop hit at a raised level is "TRAIL", a profit locked in). A candle is judged with the stop in force at its start, and the stop touched counts first when the same candle also reached a trail level or the target (the cautious choice). Setups that
confirm while a trade is open are only listed ("ignored"); once the trade is closed the next setup becomes the next trade. No new trade starts
after CUTOFF, after MAX_TRADES trades in the day, or once the closed trades of the day add up to -MAX_LOSS_PCT percent. A 1-minute option candle that touches both levels counts as a stop (the cautious choice). A trade still open on the last candle of
the day is closed at that price. Percentages only; brokerage, taxes and slippage are not included. Pure functions, no network."""
import pandas as pd

TARGET, STOP = 0.40, 0.10
TRAIL = ((0.35, 0.30), (0.30, 0.15), (0.20, 0.05))     # trailing stop: once the premium has reached +35% / +30% / +20% the stop moves up to +30% / +15% / +5% (never down)
CUTOFF = (14, 45)
MAX_TRADES, MAX_LOSS_PCT = 5, 30.0     # daily limits: no new trade after 5 trades or once the day's closed trades add up to -30%
LAST_MINUTE = (15, 29)


def limits(day):
    """(cutoff, last minute) for a date. A regular day keeps the fixed values; a special session (Muhurat) is only about an hour long, so there the last new
    trade is 10 minutes before its close and an open trade is closed on the session's last minute."""
    try:
        import nse_calendar
        ss = nse_calendar.sessions(day)
        if ss and ss[0] != nse_calendar.REGULAR:
            c = ss[-1][1][0] * 60 + ss[-1][1][1]
            return divmod(c - 10, 60), divmod(c - 1, 60)
    except Exception:
        pass
    return CUTOFF, LAST_MINUTE


def _hm(t):
    return (t.hour, t.minute)


EARLIEST = (9, 25)      # no trade in the first 10 minutes after the 09:15 open (the setup is still listed, as "no trade before 9:25")


def _earliest(day):
    """First minute a trade may start. Regular days only; a special session (Muhurat) keeps no such wait."""
    try:
        import nse_calendar
        ss = nse_calendar.sessions(day)
        if ss and ss[0] != nse_calendar.REGULAR:
            return (0, 0)
    except Exception:
        pass
    return EARLIEST


def _take(s, df, n, last_minute=LAST_MINUTE):
    """Open (and, if the candles allow, close) a trade for setup s on the option candles df (columns o h l c). None when there is no price."""
    if df is None or df.empty:
        return None
    t = s["time"]
    before = df[df.index <= t]
    if before.empty:
        return None
    entry = float(before["c"].iloc[-1])
    if entry <= 0:
        return None
    tp, sl = entry * (1 + TARGET), entry * (1 - STOP)
    tr = {"n": n, "side": s["side"], "strike": s["strike"], "level": s["level"] + s["tag"], "entry_time": t, "entry": entry, "target": tp, "stop": sl, "stop_now": sl,
          "status": "OPEN", "exit_time": None, "exit": None, "last": entry}
    for ts, r in df[df.index > t].iterrows():
        tr["last"] = float(r["c"])
        if r["l"] <= sl:                                    # touched the stop in force at the start of this candle (also when the same candle reached the target or a trail level)
            tr.update(status="STOP" if sl < entry else "TRAIL", exit_time=ts, exit=min(sl, float(r["o"])))      # a gap down through the stop fills at the open
            break
        if r["h"] >= tp:
            tr.update(status="TARGET", exit_time=ts, exit=tp)
            break
        for reach, lock in TRAIL:                           # the stop moves up for the next candle; the first (highest) level reached counts
            if r["h"] >= entry * (1 + reach):
                sl = max(sl, entry * (1 + lock))
                break
        tr["stop_now"] = sl
    if tr["status"] == "OPEN" and _hm(df.index[-1]) >= last_minute:
        tr.update(status="DAY END", exit_time=df.index[-1], exit=float(df["c"].iloc[-1]))
    px = tr["exit"] if tr["exit"] is not None else tr["last"]
    tr["pnl_pct"] = round((px / entry - 1) * 100, 1)
    return tr


def run_trades(setups, option_frame, cutoff=None):
    """setups: chronological list from stream_setups.find_setups. option_frame(setup) -> DataFrame of that contract's 1-minute candles or None.
    Returns {"trades": [...], "signals": [...], "total_pct": float}; signals lists every setup with what happened to it."""
    trades, signals, cur = [], [], None
    last_minute = LAST_MINUTE
    if setups:
        lim = limits(setups[0]["time"].date())
        cutoff, last_minute = (cutoff or lim[0]), lim[1]
    cutoff = cutoff or CUTOFF
    earliest = _earliest(setups[0]["time"].date()) if setups else (0, 0)
    for s in setups:
        t = s["time"]
        if _hm(t) < earliest:                                # the first minutes after the open: the setup is listed, not traded
            status = f"no trade before {earliest[0]}:{earliest[1]:02d}"
        elif cur is not None and (cur["exit_time"] is None or t < cur["exit_time"]):
            status = f"ignored: trade {cur['n']} is open"
        elif len(trades) >= MAX_TRADES:
            status = f"daily limit: {MAX_TRADES} trades done"
        elif sum(x["pnl_pct"] for x in trades) <= -MAX_LOSS_PCT:
            status = f"daily loss limit reached (-{MAX_LOSS_PCT:.0f}%)"
        elif _hm(t) > cutoff:
            status = f"no new trade after {cutoff[0]}:{cutoff[1]:02d}"
        else:
            tr = _take(s, option_frame(s), len(trades) + 1, last_minute)
            if tr is None:
                status = "no option price"
            else:
                trades.append(tr)
                cur = tr
                status = f"TRADE {tr['n']}"
        signals.append({"time": t, "side": s["side"], "strike": s["strike"], "level": s["level"] + s["tag"], "status": status})
    return {"trades": trades, "signals": signals, "total_pct": round(sum(tr["pnl_pct"] for tr in trades), 1), "cutoff": "%d:%02d" % tuple(cutoff)}


def to_json(res, shift):
    """JSON-ready copy (times as HH:MM, chart marker times shifted like the candles)."""
    hm = lambda t: None if t is None else t.strftime("%H:%M")
    ep = lambda t: int(t.timestamp()) + shift
    trades = [{**{k: v for k, v in tr.items() if k not in ("entry_time", "exit_time")}, "entry_time": hm(tr["entry_time"]), "exit_time": hm(tr["exit_time"]),
               "entry": round(tr["entry"], 2), "target": round(tr["target"], 2), "stop": round(tr["stop"], 2), "stop_now": round(tr.get("stop_now", tr["stop"]), 2),
               "exit": None if tr["exit"] is None else round(tr["exit"], 2), "last": round(tr["last"], 2)} for tr in res["trades"]]
    markers = []
    for tr in res["trades"]:
        markers.append({"time": ep(tr["entry_time"]), "kind": "entry", "side": tr["side"], "text": f"T{tr['n']} {tr['side']}"})
        if tr["exit_time"] is not None:
            markers.append({"time": ep(tr["exit_time"]), "kind": "exit", "side": tr["side"], "text": f"T{tr['n']} {tr['status']} {tr['pnl_pct']:+.0f}%"})
    sig = [{"when": hm(x["time"]), "time": ep(x["time"]), "side": x["side"], "strike": f"{x['strike']} {'CE' if x['side'] == 'CALL' else 'PE'}", "level": x["level"],
            "status": x["status"]} for x in res["signals"]]
    return {"trades": trades, "signals": sig, "markers": markers, "total_pct": res["total_pct"], "cutoff": res.get("cutoff", f"{CUTOFF[0]}:{CUTOFF[1]:02d}")}
