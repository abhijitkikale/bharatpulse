"""Trading journal backup for the stream page: one JSON file per trading day (data/journal/YYYY-MM-DD.json) with everything needed to review each paper
trade later, and a running data/journal/trades.csv with one row per trade (opens in Excel). Kept on this PC only; data/ is not part of the public repository.

A day file holds: the end-of-day levels that were valid that session, where the opening candles came from, the regime, the last live GEX snapshot,
every setup with what happened to it (taken / ignored while a trade was open / after the cutoff), every trade with its contract (symbol, expiry),
entry / target / stop / exit prices, the result, the option's 1-minute candles from entry to exit, and the day's 1-minute index candles."""
import csv
import datetime as dt
import json
import pathlib

CSV_FIELDS = ["date", "trade", "side", "strike", "contract", "expiry", "setup", "entry_time", "entry", "target", "stop", "exit_time", "exit", "result", "pnl_pct",
              "setups_ignored_while_open"]


def candle_rows(df, since=None, until=None):
    d = df
    if since is not None:
        d = d[d.index >= since]
    if until is not None:
        d = d[d.index <= until]
    return [[t.strftime("%H:%M"), round(float(r.o), 2), round(float(r.h), 2), round(float(r.l), 2), round(float(r.c), 2), int(r.v) if r.v == r.v else 0] for t, r in d.iterrows()]


def build_day(day, res, replay, index_df, opt_frames, meta, extra=None):
    """res: stream_trades.run_trades() result (with Timestamps); opt_frames: {(strike, 'CE'/'PE'): candles}; meta: {(strike, 'CE'/'PE'): {symbol, expiry}}."""
    trades = []
    for tr in res["trades"]:
        key = (float(tr["strike"]), "CE" if tr["side"] == "CALL" else "PE")
        frame = opt_frames.get(key)
        rows = candle_rows(frame, tr["entry_time"], tr["exit_time"]) if frame is not None else []
        m = meta.get(key, {})
        ign = [s for s in res["signals"] if s["status"] == f"ignored: trade {tr['n']} is open"]
        trades.append({"n": tr["n"], "side": tr["side"], "strike": tr["strike"], "contract": m.get("symbol"), "expiry": m.get("expiry"), "setup": tr["level"],
                       "entry_time": tr["entry_time"].strftime("%H:%M"), "entry": round(tr["entry"], 2), "target": round(tr["target"], 2), "stop": round(tr["stop"], 2),
                       "exit_time": tr["exit_time"].strftime("%H:%M") if tr["exit_time"] is not None else None,
                       "exit": None if tr["exit"] is None else round(tr["exit"], 2), "result": tr["status"], "pnl_pct": tr["pnl_pct"],
                       "setups_ignored_while_open": [f"{s['time']:%H:%M} {s['side']} {s['strike']} {s['level']}" for s in ign], "option_candles_hm_ohlcv": rows})
    return {"date": str(day), "symbol": "NIFTY", "levels_used": {"close": (replay or {}).get("close"), "levels": (replay or {}).get("levels")},
            "total_pct": res["total_pct"], "trades": trades,
            "setups": [{"time": s["time"].strftime("%H:%M"), "side": s["side"], "strike": s["strike"], "level": s["level"], "status": s["status"]} for s in res["signals"]],
            "index_candles_hm_ohlcv": candle_rows(index_df), **(extra or {})}


def write(day_doc, folder):
    """Write the day's file and rebuild trades.csv from all day files. Returns the day file's path."""
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    doc = {**day_doc, "saved_at": dt.datetime.now().isoformat(timespec="seconds")}
    f = folder / (day_doc["date"] + ".json")
    f.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    rebuild_csv(folder)
    return f


def rebuild_csv(folder):
    folder = pathlib.Path(folder)
    with open(folder / "trades.csv", "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        w.writeheader()
        for f in sorted(folder.glob("????-??-??.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            for tr in d.get("trades", []):
                w.writerow({"date": d["date"], "trade": tr["n"], "side": tr["side"], "strike": tr["strike"], "contract": tr.get("contract"), "expiry": tr.get("expiry"),
                            "setup": tr["setup"], "entry_time": tr["entry_time"], "entry": tr["entry"], "target": tr["target"], "stop": tr["stop"], "exit_time": tr["exit_time"],
                            "exit": tr["exit"], "result": tr["result"], "pnl_pct": tr["pnl_pct"], "setups_ignored_while_open": len(tr.get("setups_ignored_while_open", []))})
