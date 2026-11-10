"""Pure helpers for the live stream page (tools/stream_server.py): end-of-day levels from the dashboard data, today's developing
volume levels and the two VWAPs from 1-minute candles, and the state dict the page draws. No network here, so it can be tested."""
import json

import pandas as pd

import compute_options
from live_gex import LEVELS as LIVE_LEVELS

ROWS = 0.25                  # volume-profile row size in points (close to TradingView's own very fine rows)
SHIFT = 19800                # the chart axis is UTC: add 5.5 h so it reads in IST


def parse_pages(html):
    """The dashboard page embeds its data as `const D = {...};`. Returns that dict."""
    marker = "const D = "
    i = html.find(marker)
    if i < 0:
        raise ValueError("no embedded data in the page")
    d, _ = json.JSONDecoder().raw_decode(html[i + len(marker):])
    return d


def eod_levels(D, sym="NIFTY"):
    """The end-of-day levels exactly as the Options tab's 'Levels for TradingView' text builds them (first expiry, nearest-expiry GEX)."""
    blk = D["options"]["symbols"][sym]
    ch = blk["chain"]
    ex = ch["expiries"][0]
    g = (blk.get("gex") or {}).get("nearest")
    spot = float(ch["spot"])
    ks, coi, poi = ex["k"], ex["coi"], ex["poi"]
    up = [i for i, k in enumerate(ks) if k >= spot and coi[i] > 0]
    dn = [i for i, k in enumerate(ks) if k <= spot and poi[i] > 0]
    lv = {"Spot": spot, "Max pain": ex.get("pain"),
          "Gamma flip": g and g.get("flip"),
          "OI call wall": ks[max(up, key=lambda i: coi[i])] if up else None,
          "OI put wall": ks[max(dn, key=lambda i: poi[i])] if dn else None,
          "GEX call wall": g and g["walls"].get("call_gex"), "GEX put wall": g and g["walls"].get("put_gex")}
    vp = blk.get("vp") or {}
    d1, d5 = vp.get("1"), vp.get("5")
    if d1:
        lv.update({"POC": d1["poc"], "VAH": d1["vah"], "VAL": d1["val"]})
    if d5:
        lv.update({"Weekly POC": d5["poc"], "Weekly VAH": d5["vah"], "Weekly VAL": d5["val"]})
    flip = lv["Gamma flip"]
    pos = bool(g and g["total_cr"] > 0)
    pos_above = None
    if g and flip is not None:
        above = spot >= flip
        pos_above = above if pos else not above
    return {"close": ch["asof"], "levels": {k: round(float(v), 2) for k, v in lv.items() if v is not None},
            "positive": pos if g else None, "pos_above": pos_above}


def now_levels(df, rows=ROWS):
    """Developing volume levels from 1-minute candles (columns o h l c v, tz-aware IST index): today so far and the last 5 sessions."""
    d = df.dropna(subset=["h", "l", "v"])
    d = d[d["v"] > 0]
    days = sorted(set(d.index.date))
    out = {}
    if not days:
        return out
    today = d[[x == days[-1] for x in d.index.date]]
    p = compute_options._profile(today, rows, sessions=1)
    if p:
        out.update({"Now POC": p["poc"], "Now VAH": p["vah"], "Now VAL": p["val"]})
    keep = set(days[-5:])
    p = compute_options._profile(d[[x in keep for x in d.index.date]], rows, sessions=len(keep))
    if p and len(keep) >= 1:
        out.update({"Now Weekly POC": p["poc"], "Now Weekly VAH": p["vah"], "Now Weekly VAL": p["val"]})
    return out


def vwaps(df):
    """(daily, weekly) VWAP series on hlc3: the daily one restarts each session, the weekly one each ISO week (Monday)."""
    d = df.dropna(subset=["h", "l", "c", "v"])
    d = d[d["v"] > 0]
    tp = (d["h"] + d["l"] + d["c"]) / 3
    pv = tp * d["v"]
    day = pd.Series([str(x) for x in d.index.date], index=d.index)
    iso = [x.isocalendar() for x in d.index]
    wk = pd.Series([f"{i[0]}-{i[1]}" for i in iso], index=d.index)
    daily = pv.groupby(day).cumsum() / d["v"].groupby(day).cumsum()
    weekly = pv.groupby(wk).cumsum() / d["v"].groupby(wk).cumsum()
    return daily, weekly


def live_levels(block):
    """{name: value} for the live GEX block of live_gex.build_gex (the OI walls are end-of-day only)."""
    return {"Live " + label: block["levels"][k] for k, label in LIVE_LEVELS if block["levels"].get(k) is not None}


def build_state(df, eod, live_block, now, status, sessions=2, setups=None, trades=None):
    """The dict the page draws. df: 1-minute candles (several sessions); eod: eod_levels() or None; live_block: one live_gex block or None."""
    days = sorted(set(df.index.date))
    view = df[[x in set(days[-sessions:]) for x in df.index.date]]
    daily, weekly = vwaps(df)
    ser = lambda s: [{"time": int(t.timestamp()) + SHIFT, "value": round(float(v), 2)} for t, v in s.loc[s.index.isin(view.index)].items() if pd.notna(v)]
    last = float(df["c"].iloc[-1])
    levels = []
    if eod:
        levels += [{"name": k, "value": v, "kind": "eod"} for k, v in eod["levels"].items() if k != "Spot"]
    if live_block:
        levels += [{"name": k, "value": v, "kind": "live"} for k, v in live_levels(live_block).items()]
    levels += [{"name": k, "value": v, "kind": "now"} for k, v in now_levels(df).items()]
    for name, s in (("Now VWAP", daily), ("Now Weekly VWAP", weekly)):
        if len(s) and pd.notna(s.iloc[-1]):
            levels.append({"name": name, "value": round(float(s.iloc[-1]), 2), "kind": "now"})
    regime = {"eod": None, "now": None, "now_time": None}
    if eod and eod["positive"] is not None:
        regime["eod"] = "POSITIVE" if eod["positive"] else "NEGATIVE"
    if live_block:
        flip = live_block["levels"].get("flip")
        pos = live_block["net_cr"] > 0
        if flip is not None:
            above = live_block["spot"] >= flip
            pos_above = above if pos else not above
            pos = (last > flip) == pos_above                      # today's price against the live gamma flip
        regime["now"] = "POSITIVE" if pos else "NEGATIVE"
        regime["now_time"] = live_block.get("time")
    sg = setups or []
    setup_out = {"table": [{"when": x["time"].strftime("%d %b %H:%M"), "side": x["side"], "strike": f"{x['strike']} {'CE' if x['side'] == 'CALL' else 'PE'}",
                            "level": x["level"] + x["tag"]} for x in reversed(sg[-6:])],
                 "markers": [{"time": int(x["time"].timestamp()) + SHIFT, "side": x["side"], "text": f"{x['side']}{x['tag'] or ' ✓'}"} for x in sg]}
    return {"asof": now.isoformat(timespec="seconds"), "setups": setup_out, "trades": trades, "last": round(last, 2), "last_candle": df.index.max().strftime("%H:%M"),
            "date": str(days[-1]), "status": status, "eod_close": eod["close"] if eod else None,
            "candles": [{"time": int(t.timestamp()) + SHIFT, "open": float(r.o), "high": float(r.h), "low": float(r.l), "close": float(r.c)} for t, r in view.iterrows()],
            "vwap": {"daily": ser(daily), "weekly": ser(weekly)}, "levels": levels, "regime": regime}


OPEN_START, OPEN_MINUTES = (9, 15), 30            # the opening candles TradingView may send: 09:15 up to 09:45


def parse_open_bar(text, day, max_days=0):
    """One message from the TradingView alert: {"t": bar open time in ms, "o","h","l","c","v"}. Returns (IST minute Timestamp, bar dict) or None when
    it is not a sane 1-minute candle inside the opening window of a date from `day` back to `day` minus max_days (`day` is a datetime.date)."""
    try:
        j = json.loads(text)
        o, h, l, c, v = (float(j[k]) for k in ("o", "h", "l", "c", "v"))
        ts = pd.Timestamp(int(j["t"]), unit="ms", tz="UTC").tz_convert("Asia/Kolkata")
    except Exception:
        return None
    mins = ts.hour * 60 + ts.minute - (OPEN_START[0] * 60 + OPEN_START[1])
    if not 0 <= (day - ts.date()).days <= max_days or ts.second != 0 or not 0 <= mins < OPEN_MINUTES:
        return None
    if not all(x == x and x > 0 for x in (o, h, l, c, v)) or h < max(o, c, l) or l > min(o, c, h):
        return None
    return ts, {"o": o, "h": h, "l": l, "c": c, "v": v}


def merge_open(df, bars, max_dev=0.05):
    """Replace / add the opening candles in the Fyers candles df with TradingView's own (bars: {Timestamp: bar}). Bars further than max_dev
    from the day's typical price are ignored. Returns (new frame, number of TradingView candles used)."""
    if not bars:
        return df, 0
    out = df.copy()
    ref = float(df["c"].median())
    used = 0
    for ts, b in sorted(bars.items()):
        if abs(b["c"] / ref - 1) > max_dev:
            continue
        out.loc[ts, ["o", "h", "l", "c", "v"]] = [b["o"], b["h"], b["l"], b["c"], b["v"]]
        used += 1
    return out.sort_index(), used


def parse_signal(text, day, max_days=4, step=50):
    """One setup message from the TradingView alert: {"k": "sig", "t": bar open ms, "s": CALL / PUT, "l": level name, "v": level value, "g": tag, "c": close}.
    Returns the setup dict the page uses ({"idx", "time", "side", "strike", "level", "tag"}) or None when it is not such a message."""
    try:
        j = json.loads(text)
        if j.get("k") != "sig" or j["s"] not in ("CALL", "PUT"):
            return None
        ts = pd.Timestamp(int(j["t"]), unit="ms", tz="UTC").tz_convert("Asia/Kolkata")
        c, v = float(j["c"]), float(j["v"])
        name = str(j["l"])[:40]
        tag = str(j.get("g", ""))[:20]
    except Exception:
        return None
    if not 0 <= (day - ts.date()).days <= max_days or ts.second != 0 or not (c == c and c > 0 and v == v and v > 0):
        return None
    return {"idx": -1, "time": ts, "side": j["s"], "strike": int(round(c / step) * step), "level": f"{name} {v:.2f}", "tag": tag}
