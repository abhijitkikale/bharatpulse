"""Paper trade tracker for the stream page: one trade at a time, taken from the setups of stream_setups.find_setups.

The first setup becomes a trade (CALL = buy the at-the-money call, PUT = buy the at-the-money put of the nearest expiry). Its entry price is the
option's 1-minute close on the setup candle. The trade stays open until +TARGET (profit) or -STOP (loss) of that premium is touched. Setups that
confirm while a trade is open are only listed ("ignored"); once the trade is closed the next setup becomes the next trade. No new trade starts
after CUTOFF. A 1-minute option candle that touches both levels counts as a stop (the cautious choice). A trade still open on the last candle of
the day is closed at that price. Percentages only; brokerage, taxes and slippage are not included. Pure functions, no network."""
import pandas as pd

TARGET, STOP = 0.40, 0.10
CUTOFF = (14, 45)
LAST_MINUTE = (15, 29)


def _hm(t):
    return (t.hour, t.minute)


def _take(s, df, n):
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
    tr = {"n": n, "side": s["side"], "strike": s["strike"], "level": s["level"] + s["tag"], "entry_time": t, "entry": entry, "target": tp, "stop": sl,
          "status": "OPEN", "exit_time": None, "exit": None, "last": entry}
    for ts, r in df[df.index > t].iterrows():
        tr["last"] = float(r["c"])
        if r["l"] <= sl:                                    # touched the stop (also when the same candle reached the target)
            tr.update(status="STOP", exit_time=ts, exit=min(sl, float(r["o"])))      # a gap down through the stop fills at the open
            break
        if r["h"] >= tp:
            tr.update(status="TARGET", exit_time=ts, exit=tp)
            break
    if tr["status"] == "OPEN" and _hm(df.index[-1]) >= LAST_MINUTE:
        tr.update(status="DAY END", exit_time=df.index[-1], exit=float(df["c"].iloc[-1]))
    px = tr["exit"] if tr["exit"] is not None else tr["last"]
    tr["pnl_pct"] = round((px / entry - 1) * 100, 1)
    return tr


def run_trades(setups, option_frame, cutoff=CUTOFF):
    """setups: chronological list from stream_setups.find_setups. option_frame(setup) -> DataFrame of that contract's 1-minute candles or None.
    Returns {"trades": [...], "signals": [...], "total_pct": float}; signals lists every setup with what happened to it."""
    trades, signals, cur = [], [], None
    for s in setups:
        t = s["time"]
        if cur is not None and (cur["exit_time"] is None or t < cur["exit_time"]):
            status = f"ignored: trade {cur['n']} is open"
        elif _hm(t) > cutoff:
            status = f"no new trade after {cutoff[0]}:{cutoff[1]:02d}"
        else:
            tr = _take(s, option_frame(s), len(trades) + 1)
            if tr is None:
                status = "no option price"
            else:
                trades.append(tr)
                cur = tr
                status = f"TRADE {tr['n']}"
        signals.append({"time": t, "side": s["side"], "strike": s["strike"], "level": s["level"] + s["tag"], "status": status})
    return {"trades": trades, "signals": signals, "total_pct": round(sum(tr["pnl_pct"] for tr in trades), 1)}


def to_json(res, shift):
    """JSON-ready copy (times as HH:MM, chart marker times shifted like the candles)."""
    hm = lambda t: None if t is None else t.strftime("%H:%M")
    ep = lambda t: int(t.timestamp()) + shift
    trades = [{**{k: v for k, v in tr.items() if k not in ("entry_time", "exit_time")}, "entry_time": hm(tr["entry_time"]), "exit_time": hm(tr["exit_time"]),
               "entry": round(tr["entry"], 2), "target": round(tr["target"], 2), "stop": round(tr["stop"], 2),
               "exit": None if tr["exit"] is None else round(tr["exit"], 2), "last": round(tr["last"], 2)} for tr in res["trades"]]
    markers = []
    for tr in res["trades"]:
        markers.append({"time": ep(tr["entry_time"]), "kind": "entry", "side": tr["side"], "text": f"T{tr['n']} {tr['side']}"})
        if tr["exit_time"] is not None:
            markers.append({"time": ep(tr["exit_time"]), "kind": "exit", "side": tr["side"], "text": f"T{tr['n']} {tr['status']} {tr['pnl_pct']:+.0f}%"})
    sig = [{"when": hm(x["time"]), "time": ep(x["time"]), "side": x["side"], "strike": f"{x['strike']} {'CE' if x['side'] == 'CALL' else 'PE'}", "level": x["level"],
            "status": x["status"]} for x in res["signals"]]
    return {"trades": trades, "signals": sig, "markers": markers, "total_pct": res["total_pct"], "cutoff": f"{CUTOFF[0]}:{CUTOFF[1]:02d}"}
