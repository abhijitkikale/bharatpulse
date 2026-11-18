"""The King analysis of one trading day for the trading journal: where the King node was through the day, the candle that first touched each King, what price did
next, whether price drifted toward the King (against control levels), and where the day closed relative to it. Built from the nodes agent's saved readings
(data/nodes/YYYY-MM-DD.jsonl) and the journal's 1-minute index candles; nothing is fetched. Plain numbers and sentences, no advice."""
import bisect

import pandas as pd

import nodes
import nodes_ledger as L
import nodes_pull as P

MIN_RUN = 3            # a King that lasts fewer readings (about 6 minutes) is a flicker, listed as such but not analysed
TOUCH = L.APPROACH     # a candle whose range comes within this many points of the King strike "reached" it (the same 15 points as the reaction ledger)
CLOSE_LAG_MIN = 5      # the King "at the close" is read this many minutes before the last candle: expiry-day chains are meaningless in the last minutes
MAX_AGE = 6            # minutes: the newest reading before a trade must be at most this old
SIGNIFICANT_MIN = 10   # the day's summary names the Kings that held for at least this many minutes


def _runs(readings):
    runs = []
    for r in readings:
        cl = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])
        k = cl["structure"].get("king")
        if k is None:
            continue
        kn = [n for n in cl["nodes"] if "king" in n["roles"]]
        sign = kn[0]["sign"] if kn else "+"
        if runs and runs[-1]["king"] == k:
            runs[-1]["end"], runs[-1]["n"], runs[-1]["end_spot"] = r["t"], runs[-1]["n"] + 1, r["spot"]
        else:
            runs.append({"king": k, "sign": sign, "start": r["t"], "end": r["t"], "n": 1, "start_spot": r["spot"], "end_spot": r["spot"]})
    return runs


def map_rows(readings):
    """One row per reading for the King chart: [from, to, King, sign, floor, ceiling, negative-gamma 0/1]; each row lasts until the next reading (the last one 2 minutes)."""
    rows = []
    for r in readings:
        cl = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])
        st = cl["structure"]
        if st.get("king") is None:
            continue
        kn = [n for n in cl["nodes"] if "king" in n["roles"]]
        rows.append([r["t"], None, st["king"], kn[0]["sign"] if kn else "+", st.get("floor"), st.get("ceiling"), 1 if r["total_cr"] < 0 else 0])
    for i, x in enumerate(rows):
        x[1] = rows[i + 1][0] if i + 1 < len(rows) else x[0] + pd.Timedelta(minutes=2)
        x[0], x[1] = _hm(x[0]), _hm(x[1])
    return rows


def _hm(ts):
    return ts.strftime("%H:%M")


def _candle_for_run(run, candles):
    """The candle that first came within TOUCH points of the King strike during its run (with what followed), or, when none did, the candle that came closest."""
    k = run["king"]
    idx = list(candles.index)
    a = bisect.bisect_left(idx, run["start"].floor("min"))
    b = bisect.bisect_right(idx, run["end"])
    best = None
    for i in range(a, b):
        row = candles.iloc[i]
        hi, lo = float(row["h"]), float(row["l"])
        gap = 0.0 if lo - TOUCH <= k <= hi + TOUCH else min(abs(lo - k), abs(hi - k)) - TOUCH
        if gap == 0.0:
            best = (0.0, i)
            break
        if best is None or gap < best[0]:
            best = (gap, i)
    if best is None:
        return {"reached": False, "closest": None}
    gap, i = best
    row = candles.iloc[i]
    prev = float(candles["c"].iloc[i - 1]) if i > 0 else float(row["o"])
    c = {"reached": gap == 0.0, "time": _hm(idx[i]), "o": float(row["o"]), "h": float(row["h"]), "l": float(row["l"]), "c": float(row["c"]),
         "closed": "above" if float(row["c"]) > k else "below", "from": "above" if prev > k else "below"}
    if gap == 0.0:
        c["outcome"], c["up_pts"], c["down_pts"] = L._outcome(candles, i, k, prev > k)
    else:
        c["closest"] = round(gap + TOUCH, 1)
    return c


def trades_vs_king(readings, trades, day):
    """For each taken trade: the King at its entry, and whether the trade pointed toward the King (a call with the King above price, a put with the King below) or away from it."""
    if not readings or not trades:
        return []
    times = [r["t"] for r in readings]
    out = []
    for t in trades:
        if not t.get("entry_time"):
            continue
        ts = pd.Timestamp(f"{day} {t['entry_time']}", tz="Asia/Kolkata")
        i = bisect.bisect_right(times, ts) - 1
        if i < 0 or ts - times[i] > pd.Timedelta(minutes=MAX_AGE):
            continue
        r = readings[i]
        k = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])["structure"].get("king")
        if k is None:
            continue
        dist = round(k - r["spot"], 1)
        out.append({"n": t.get("n"), "side": t.get("side"), "time": t["entry_time"], "king": k, "distance": dist, "with_king": (dist > 0) == (t.get("side") == "CALL"),
                    "pnl_pct": t.get("pnl_pct")})
    return out


def day_analysis(day, readings, candles, trades=None):
    """None when there is nothing to analyse; otherwise {"runs", "king_steps", "pull", "trades", "lines", "close"}."""
    if not readings or candles is None or len(candles) < 3:
        return None
    tv = trades_vs_king(readings, trades, day)
    readings = [r for r in readings if r["t"] <= candles.index[-1] - pd.Timedelta(minutes=CLOSE_LAG_MIN)]      # the last minutes of an expiry day have no usable chain
    runs = _runs(readings)
    if not runs:
        return None
    out_runs, steps = [], []
    for r in runs:
        mins = max(1, round((r["end"] - r["start"]).total_seconds() / 60.0) + 2)       # readings are about two minutes apart
        steps.append([_hm(r["start"]), _hm(r["end"] + pd.Timedelta(minutes=2)), r["king"], r["sign"]])
        if r["n"] < MIN_RUN:
            continue
        item = {"king": r["king"], "sign": r["sign"], "from": _hm(r["start"]), "to": _hm(r["end"]), "readings": r["n"], "minutes": mins,
                "spot_start": round(r["start_spot"], 1), "spot_end": round(r["end_spot"], 1), "distance": round(r["king"] - r["start_spot"], 1)}
        item["toward"] = round(abs(r["start_spot"] - r["king"]) - abs(r["end_spot"] - r["king"]), 1)
        item["candle"] = _candle_for_run(r, candles)
        out_runs.append(item)
    samples = P.samples_for_day(day, readings, candles)
    pull = {}
    for h in P.HORIZONS:
        sub = [s for s in samples if s["h"] == h]
        if not sub:
            continue
        rows = {x["level"]: x for x in P._table(sub)}
        pull[str(h)] = {"n": len(sub), "independent": P._independent(sub),
                        "king": rows["king"], "shift50": rows.get("shift50"), "vwap": rows.get("vwap"), "mid": rows.get("mid")}
    last = candles.iloc[-1]
    close_t = candles.index[-1]
    k_close = _runs([readings[-1]])[0]["king"]
    first_king = runs[0]["king"]
    close = {"price": round(float(last["c"]), 2), "time": _hm(close_t), "king": k_close, "first_king": first_king,
             "first_king_gap_high": round(abs(float(candles["h"].max()) - first_king), 1), "first_king_gap_low": round(abs(float(candles["l"].min()) - first_king), 1), "away": round(float(last["c"]) - k_close, 1),
             "high": round(float(candles["h"].max()), 2), "low": round(float(candles["l"].min()), 2), "open": round(float(candles["o"].iloc[0]), 2)}
    ev = [[e["time"], e["k"], e["outcome"], e["role"], e["sign"]] for e in sorted(L.events_for_day(day, readings, candles), key=lambda e: e["time"])]
    res = {"day": day, "map": map_rows(readings), "events": ev, "trades": tv, "trades_total": len(trades or []), "runs": out_runs, "flickers": sum(1 for r in runs if r["n"] < MIN_RUN), "king_steps": steps, "pull": pull, "close": close}
    res["lines"] = _lines(res, day)
    return res


def _lines(a, day):
    """The day in plain sentences."""
    lines = []
    long_runs = [r for r in a["runs"] if r["minutes"] >= SIGNIFICANT_MIN]
    if long_runs:
        names = ", ".join(f"{r['king']:,} ({r['from']} to {r['to']})" for r in long_runs[:6])
        lines.append(f"The King held for {SIGNIFICANT_MIN} minutes or more at {names}. It changed strike {max(len(a['king_steps']) - 1, 0)} times and {a['flickers']} of those were short flickers (under about 6 minutes).")
    reached = [r for r in a["runs"] if r["candle"].get("reached")]
    if reached:
        parts = []
        for r in reached:
            c = r["candle"]
            parts.append(f"{r['king']:,} at {c['time']} (candle {c['o']:.0f}/{c['h']:.0f}/{c['l']:.0f}/{c['c']:.0f}, came from {c['from']}, closed {c['closed']}, then "
                         f"{'turned back' if c['outcome'] == 'held' else 'went through' if c['outcome'] == 'passed' else 'chopped around it'})")
        lines.append("Price came within 15 points of the King during its run: " + "; ".join(parts) + ".")
    never = [r for r in long_runs if not r["candle"].get("reached")]
    if never:
        lines.append("Price did not come within 15 points of the King during: " + ", ".join(f"{r['king']:,} ({r['from']} to {r['to']}, closest {r['candle'].get('closest')} points at {r['candle'].get('time')})" for r in never) + ".")
    p30 = a["pull"].get("30")
    if p30:
        k, s = p30["king"], p30["shift50"]
        lines.append(f"Drift toward the King over 30 minutes: price was closer in {k['closer']:.0f}% of {p30['n']} readings (average {k['gain']:+.1f} points); the King moved 50 points either way scored {s['closer']:.0f}% ({s['gain']:+.1f}). "
                     f"That is about {p30['independent']} independent windows, so one day is a sample, not a verdict.")
    tv = a.get("trades") or []
    if tv:
        w = [t for t in tv if t["with_king"]]
        ag = [t for t in tv if not t["with_king"]]
        fmt = lambda L_: ", ".join(f"{t['side'].lower()} {t['time']} {t['pnl_pct']:+.0f}%" for t in L_) or "none"
        miss = a.get("trades_total", len(tv)) - len(tv)
        lines.append(f"Trades by direction{f' ({miss} more had no King reading in the 6 minutes before entry)' if miss > 0 else ''}: {len(w)} pointed toward the King ({fmt(w)}) and {len(ag)} away from it ({fmt(ag)}).")
    c = a["close"]
    g = min(c["first_king_gap_high"], c["first_king_gap_low"])
    lines.append(f"The King at the first reading was {c['first_king']:,}; the day's {'high' if c['first_king_gap_high'] <= c['first_king_gap_low'] else 'low'} came within {g:.0f} point{'' if round(g) == 1 else 's'} of it.")
    lines.append(f"The day closed at {c['price']:,.2f} ({c['time']}), {abs(c['away']):.0f} points {'above' if c['away'] > 0 else 'below'} the King of {c['king']:,} (read at {CLOSE_LAG_MIN} minutes before the close). Range {c['low']:,.0f} to {c['high']:,.0f}.")
    return lines


def all_days(nodes_dir, journal_dir):
    """{day: analysis} for every saved nodes day that has candles."""
    import json
    import pathlib
    res = {}
    for f in sorted(pathlib.Path(nodes_dir).glob("????-??-??.jsonl")):
        try:
            trades = json.loads((pathlib.Path(journal_dir) / f"{f.stem}.json").read_text(encoding="utf-8")).get("trades") or []
        except (OSError, ValueError):
            trades = []
        a = day_analysis(f.stem, L.load_readings(f), L.load_candles(f.stem, journal_dir), trades)
        if a:
            res[f.stem] = a
    return res
