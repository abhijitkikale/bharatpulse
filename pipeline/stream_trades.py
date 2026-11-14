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


# Rules by the time a trade is ENTERED (it keeps them until it closes). From 13:00 IST: target +20%, stop -7%, and the stop trails at +10% (to +5%) and +15% (to +10%).
RULES = (((0, 0), {"target": TARGET, "stop": STOP, "trail": TRAIL, "name": "morning"}),
         ((13, 0), {"target": 0.20, "stop": 0.07, "trail": ((0.15, 0.10), (0.10, 0.05)), "name": "afternoon"}))
CONTRA_IN_NEGATIVE = True   # in NEGATIVE gamma a setup against the move (CALL below the gamma flip, PUT above it) is listed, not traded
REGIME_BUFFER = 0.05        # when the gamma regime turns against an open trade that is in profit, its stop moves up to 5% below the current premium


def _contra(s, rg):
    """True when setup s goes against the move in negative gamma. rg: {"regime": "POSITIVE"/"NEGATIVE", "side": -1 below / +1 above the live flip, 0 unknown} or None."""
    if not CONTRA_IN_NEGATIVE or not rg or rg.get("regime") != "NEGATIVE" or not rg.get("side"):
        return False
    return (s["side"] == "CALL" and rg["side"] < 0) or (s["side"] == "PUT" and rg["side"] > 0)


EARLIEST = (9, 25)      # no trade in the first 10 minutes after the 09:15 open (the setup is still listed, as "no trade before 9:25")


def _regular(day):
    """False on a special session (Muhurat), which keeps neither the 10-minute wait nor the afternoon rules."""
    try:
        import nse_calendar
        ss = nse_calendar.sessions(day)
        if ss and ss[0] != nse_calendar.REGULAR:
            return False
    except Exception:
        pass
    return True


def _earliest(day):
    """First minute a trade may start. Regular days only; a special session (Muhurat) keeps no such wait."""
    return EARLIEST if _regular(day) else (0, 0)


def rules_for(t):
    """The target / stop / trailing rules for a trade entered at time t."""
    chosen = RULES[0][1]
    if _regular(t.date()):
        for start, r in RULES:
            if _hm(t) >= start:
                chosen = r
    return chosen


def _take(s, df, n, last_minute=LAST_MINUTE, regime_at=None):
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
    rl = rules_for(t)
    tp, sl = entry * (1 + rl["target"]), entry * (1 - rl["stop"])
    tr = {"rules": rl["name"], "tp_pct": round(rl["target"] * 100), "sl_pct": round(rl["stop"] * 100), "n": n, "side": s["side"], "strike": s["strike"], "level": s["level"] + s["tag"], "entry_time": t, "entry": entry, "target": tp, "stop": sl, "stop_now": sl,
          "status": "OPEN", "exit_time": None, "exit": None, "last": entry, "shifts": []}
    prev_reg = None
    if regime_at is not None:
        try:
            rg0 = regime_at(t)
            prev_reg = rg0["regime"] if rg0 else None
        except Exception:
            prev_reg = None
    for ts, r in df[df.index > t].iterrows():
        tr["last"] = float(r["c"])
        if r["l"] <= sl:                                    # touched the stop in force at the start of this candle (also when the same candle reached the target or a trail level)
            tr.update(status="STOP" if sl < entry else "TRAIL", exit_time=ts, exit=min(sl, float(r["o"])))      # a gap down through the stop fills at the open
            break
        if r["h"] >= tp:
            tr.update(status="TARGET", exit_time=ts, exit=tp)
            break
        for reach, lock in rl["trail"]:                     # the stop moves up for the next candle; the first (highest) level reached counts
            if r["h"] >= entry * (1 + reach):
                if entry * (1 + lock) > sl:                 # the stop moves up (it is in force from the next candle): remember when, for the page's pop-up and the journal
                    sl = entry * (1 + lock)
                    tr["shifts"].append({"time": ts, "reach": reach, "lock": lock, "stop": sl})
                break
        if regime_at is not None:                           # the gamma regime turned against the trade (PUT: now POSITIVE, CALL: now NEGATIVE) while it is in profit: tighten the stop
            try:
                rg = regime_at(ts)
            except Exception:
                rg = None
            cur_reg = rg["regime"] if rg else None
            against = (tr["side"] == "PUT" and cur_reg == "POSITIVE") or (tr["side"] == "CALL" and cur_reg == "NEGATIVE")
            if against and prev_reg is not None and cur_reg != prev_reg and float(r["c"]) > entry:
                new = float(r["c"]) * (1 - REGIME_BUFFER)
                if new > sl:
                    sl = new
                    tr["shifts"].append({"time": ts, "reach": None, "lock": new / entry - 1, "stop": new, "why": f"gamma regime turned {cur_reg}"})
            if cur_reg is not None:
                prev_reg = cur_reg
        tr["stop_now"] = sl
    if tr["status"] == "OPEN" and _hm(df.index[-1]) >= last_minute:
        tr.update(status="DAY END", exit_time=df.index[-1], exit=float(df["c"].iloc[-1]))
    px = tr["exit"] if tr["exit"] is not None else tr["last"]
    tr["pnl_pct"] = round((px / entry - 1) * 100, 1)
    return tr


def run_trades(setups, option_frame, cutoff=None, regime_at=None):
    """setups: chronological list from stream_setups.find_setups. option_frame(setup) -> DataFrame of that contract's 1-minute candles or None.
    regime_at(time) -> {"regime", "side"} or None (the live gamma regime at that time; None = unknown, then no regime rule applies).
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
        try:
            rg = regime_at(t) if regime_at else None
        except Exception:
            rg = None
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
        elif _contra(s, rg):
            status = "not taken: against the move in negative gamma"
        else:
            tr = _take(s, option_frame(s), len(trades) + 1, last_minute, regime_at)
            if tr is None:
                status = "no option price"
            else:
                trades.append(tr)
                cur = tr
                status = f"TRADE {tr['n']}"
        signals.append({"time": t, "side": s["side"], "strike": s["strike"], "level": s["level"] + s["tag"], "status": status, "regime": rg["regime"] if rg else None})
    limit = None
    if len(trades) >= MAX_TRADES:
        limit = f"Daily limit reached: {MAX_TRADES} trades taken, no more trades today"
    elif sum(x["pnl_pct"] for x in trades if x["exit_time"] is not None) <= -MAX_LOSS_PCT:
        limit = f"Daily loss limit reached (-{MAX_LOSS_PCT:.0f}%), no more trades today"
    return {"trades": trades, "signals": signals, "total_pct": round(sum(tr["pnl_pct"] for tr in trades), 1), "cutoff": "%d:%02d" % tuple(cutoff), "limit": limit}


def to_json(res, shift):
    """JSON-ready copy (times as HH:MM, chart marker times shifted like the candles)."""
    hm = lambda t: None if t is None else t.strftime("%H:%M")
    ep = lambda t: int(t.timestamp()) + shift
    trades = [{**{k: v for k, v in tr.items() if k not in ("entry_time", "exit_time", "shifts")},
               "shifts": [{"when": hm(x["time"]), "time": ep(x["time"]), "reach": None if x.get("reach") is None else round(x["reach"] * 100), "lock": round(x["lock"] * 100), "stop": round(x["stop"], 2), "why": x.get("why")} for x in tr.get("shifts", [])], "entry_time": hm(tr["entry_time"]), "exit_time": hm(tr["exit_time"]),
               "entry": round(tr["entry"], 2), "target": round(tr["target"], 2), "stop": round(tr["stop"], 2), "stop_now": round(tr.get("stop_now", tr["stop"]), 2),
               "exit": None if tr["exit"] is None else round(tr["exit"], 2), "last": round(tr["last"], 2)} for tr in res["trades"]]
    markers = []
    for tr in res["trades"]:
        markers.append({"time": ep(tr["entry_time"]), "kind": "entry", "side": tr["side"], "text": f"T{tr['n']} {tr['side']}"})
        for x in tr.get("shifts", []):
            markers.append({"time": ep(x["time"]), "kind": "shift", "side": tr["side"], "text": f"T{tr['n']} SL {round(x['lock'] * 100):+d}%" + (" (regime)" if x.get("why") else "")})
        if tr["exit_time"] is not None:
            markers.append({"time": ep(tr["exit_time"]), "kind": "exit", "side": tr["side"], "text": f"T{tr['n']} {tr['status']} {tr['pnl_pct']:+.0f}%"})
    sig = [{"when": hm(x["time"]), "time": ep(x["time"]), "side": x["side"], "strike": f"{x['strike']} {'CE' if x['side'] == 'CALL' else 'PE'}", "level": x["level"],
            "status": x["status"], "regime": x.get("regime")} for x in res["signals"]]
    return {"trades": trades, "signals": sig, "markers": markers, "total_pct": res["total_pct"], "cutoff": res.get("cutoff", f"{CUTOFF[0]}:{CUTOFF[1]:02d}"), "limit": res.get("limit"), "max_trades": MAX_TRADES}
