"""Options tab payload: put-call ratio history, open-interest by strike, max pain, participant positioning, India VIX."""
import math

import numpy as np
import pandas as pd

from gex import compute_gex


def _num(x, nd=2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def max_pain(strikes, call_oi, put_oi):
    """Strike at which option writers' total payout at expiry is smallest (calls pay s-K above K, puts pay K-s below K)."""
    k = np.asarray(strikes, dtype=float)
    c = np.asarray(call_oi, dtype=float)
    p = np.asarray(put_oi, dtype=float)
    pain = [(c * np.maximum(s - k, 0)).sum() + (p * np.maximum(k - s, 0)).sum() for s in k]
    return float(k[int(np.argmin(pain))])


def _pcr(put, call):
    return _num(put / call, 3) if call else None


def _value_area(vol, poc, pct):
    """TradingView's documented rule: start at the POC row, compare the next row above and below, add the larger one, and stop
    before the total would exceed pct of all volume. Ties go to the row closer to the POC, then to the row above."""
    n = len(vol)
    target = pct * vol.sum()
    lo = hi = poc
    acc = vol[poc]
    while True:
        up = vol[hi + 1] if hi + 1 < n else None
        dn = vol[lo - 1] if lo - 1 >= 0 else None
        if up is None and dn is None:
            break
        take_up = dn is None or (up is not None and up >= dn)          # equal volumes: both rows are the same distance away, so above wins
        cand = up if take_up else dn
        if acc + cand > target:
            break
        acc += cand
        if take_up:
            hi += 1
        else:
            lo -= 1
    return lo, hi, acc


def _profile(d, bin_size, value_pct=0.70, sessions=None, hist=False):
    """Volume profile from bars with columns h (high), l (low), v (volume): each bar's volume is spread evenly across its
    high-low range. POC = price bin with the most volume; VAH / VAL = upper / lower edge of the value area."""
    lo = np.floor(d["l"].min() / bin_size) * bin_size
    hi = max(np.ceil(d["h"].max() / bin_size) * bin_size, lo + bin_size)       # at least one bin even if every bar is flat
    edges = np.arange(lo, hi + bin_size, bin_size)
    n = len(edges) - 1
    if n < 1:
        return None
    vol = np.zeros(n)
    for r in d.itertuples():
        a, b = float(r.l), float(r.h)
        if b - a < 1e-9:
            vol[min(max(int((a - lo) // bin_size), 0), n - 1)] += r.v
            continue
        for i in range(max(int((a - lo) // bin_size), 0), min(int((b - lo) // bin_size), n - 1) + 1):
            ov = min(b, edges[i + 1]) - max(a, edges[i])
            if ov > 0:
                vol[i] += r.v * ov / (b - a)
    tot = vol.sum()
    if tot <= 0:
        return None
    poc = int(vol.argmax())
    vlo, vhi, acc = _value_area(vol, poc, value_pct)
    out = {"days": int(sessions if sessions is not None else len(d)), "from": d.index[0].strftime("%Y-%m-%d"), "to": d.index[-1].strftime("%Y-%m-%d"),
           "poc": _num((edges[poc] + edges[poc + 1]) / 2, 2), "vah": _num(edges[vhi + 1], 2), "val": _num(edges[vlo], 2), "share": round(100 * acc / tot)}
    if hist:
        out["hist"] = {"p": [_num((edges[i] + edges[i + 1]) / 2, 2) for i in range(n)], "v": [int(v) for v in vol]}
    return out


def volume_profile(df, window, bin_size, value_pct=0.70):
    """Fallback: composite profile from DAILY bars (each day's volume spread evenly over its high-low range)."""
    d = df.dropna(subset=["h", "l", "v"]).tail(window)
    d = d[d["v"] > 0]
    if len(d) < min(window, 3):
        return None
    p = _profile(d, bin_size, value_pct)
    return None if p is None else {**p, "w": window, "src": "daily"}


def intraday_block(df, bin_size, n_sessions=5):
    """From 5-minute index candles (o, h, l, c) whose volume v is the summed traded shares of the index's stocks:
    the 1-day and 5-session profiles, plus per-session bars, VWAP and histogram for the session chart."""
    d = df.dropna(subset=["o", "h", "l", "c", "v"])
    d = d[d["v"] > 0]
    days = sorted(set(d.index.date))
    if not days:
        return {}, []
    vp = {}
    last = d[[x == days[-1] for x in d.index.date]]
    p = _profile(last, bin_size, sessions=1)
    if p:
        vp["1"] = {**p, "from": str(days[-1]), "to": str(days[-1]), "w": 1, "src": "volume"}
    if len(days) >= 5:
        keep = set(days[-5:])
        p = _profile(d[[x in keep for x in d.index.date]], bin_size, sessions=5)
        if p:
            vp["5"] = {**p, "from": str(days[-5]), "to": str(days[-1]), "w": 5, "src": "volume"}
    sessions = []
    for day in days[-n_sessions:]:
        s = d[[x == day for x in d.index.date]]
        pr = _profile(s, bin_size, sessions=1, hist=True)
        if pr is None:
            continue
        typical = (s["h"] + s["l"] + s["c"]) / 3
        vwap = (typical * s["v"]).cumsum() / s["v"].cumsum()
        sessions.append({"date": str(day), "t": [x.strftime("%H:%M") for x in s.index], "o": [_num(v) for v in s["o"]], "h": [_num(v) for v in s["h"]],
                         "l": [_num(v) for v in s["l"]], "c": [_num(v) for v in s["c"]], "v": [int(v) for v in s["v"]], "vwap": [_num(v) for v in vwap],
                         "prof": {"bin": bin_size, "poc": pr["poc"], "vah": pr["vah"], "val": pr["val"], "share": pr["share"], **pr["hist"]}})
    return vp, sessions


def _vp(sym, ohlcv, intraday):
    """(profiles, sessions). 1 day and 5 sessions from 5-minute candles with index volume; 5 sessions falls back to daily bars."""
    bin_size = 5 if sym == "NIFTY" else 10
    vp, sessions = ({}, [])
    if intraday is not None:
        vp, sessions = intraday_block(intraday, bin_size)
    if not vp.get("5") and ohlcv is not None:
        vp["5"] = volume_profile(ohlcv, 5, 25 if sym == "NIFTY" else 50)
    return (vp if any(vp.values()) else None), sessions


def symbol_block(fo: pd.DataFrame, sym: str, max_expiries=4, band=0.10, ohlcv=None, intraday=None):
    d = fo[(fo["TckrSymb"] == sym)].copy()
    d["date"] = pd.to_datetime(d["TradDt"])
    opt = d[d["OptnTp"].isin(["CE", "PE"])]
    if opt.empty:
        return None
    # --- daily history: PCR by OI and by volume across all expiries ---
    g = opt.groupby(["date", "OptnTp"])[["OpnIntrst", "TtlTradgVol"]].sum().unstack()
    hist_d, pcr_oi, pcr_vol, und = [], [], [], []
    spot = opt.groupby("date")["UndrlygPric"].median()
    for day in g.index:
        hist_d.append(day.strftime("%Y-%m-%d"))
        pcr_oi.append(_pcr(g.loc[day, ("OpnIntrst", "PE")], g.loc[day, ("OpnIntrst", "CE")]))
        pcr_vol.append(_pcr(g.loc[day, ("TtlTradgVol", "PE")], g.loc[day, ("TtlTradgVol", "CE")]))
        und.append(_num(spot.loc[day]))
    # --- latest chain, nearest expiries ---
    last = opt["date"].max()
    cur = opt[opt["date"] == last].copy()
    cur["exp"] = pd.to_datetime(cur["XpryDt"])
    expiries = sorted(cur["exp"].unique())[:max_expiries]
    s0 = float(spot.loc[last])
    out_exp = []
    for e in expiries:
        x = cur[cur["exp"] == e]
        ce = x[x["OptnTp"] == "CE"].set_index("StrkPric")
        pe = x[x["OptnTp"] == "PE"].set_index("StrkPric")
        ks = sorted(set(ce.index) | set(pe.index))
        coi = [float(ce["OpnIntrst"].get(k, 0)) for k in ks]
        poi = [float(pe["OpnIntrst"].get(k, 0)) for k in ks]
        if sum(coi) + sum(poi) == 0:
            continue
        sel = [i for i, k in enumerate(ks) if abs(k / s0 - 1) <= band]
        out_exp.append({
            "e": pd.Timestamp(e).strftime("%Y-%m-%d"),
            "pain": _num(max_pain(ks, coi, poi), 0), "pcr": _pcr(sum(poi), sum(coi)),
            "coi_total": int(sum(coi)), "poi_total": int(sum(poi)),
            "k": [_num(ks[i], 0) for i in sel], "coi": [int(coi[i]) for i in sel], "poi": [int(poi[i]) for i in sel],
            "cchg": [int(ce["ChngInOpnIntrst"].get(ks[i], 0)) for i in sel],
            "pchg": [int(pe["ChngInOpnIntrst"].get(ks[i], 0)) for i in sel],
        })
    try:                      # model estimate; never allowed to break the rest of the tab
        gex = compute_gex(cur, last, s0)
    except Exception:
        gex = None
    vp_ses = _vp(sym, ohlcv, intraday)
    return {"hist": {"d": hist_d, "pcr_oi": pcr_oi, "pcr_vol": pcr_vol, "und": und},
            "chain": {"asof": last.strftime("%Y-%m-%d"), "spot": _num(s0), "expiries": out_exp}, "gex": gex,
            "vp": vp_ses[0], "intraday": vp_ses[1]}


def participant_block(part: pd.DataFrame):
    if part is None or part.empty:
        return None
    p = part.copy()
    p["fut_net"] = p["fut_long"] - p["fut_short"]
    p["call_net"] = p["call_long"] - p["call_short"]
    p["put_net"] = p["put_long"] - p["put_short"]
    p["fut_long_pct"] = 100 * p["fut_long"] / (p["fut_long"] + p["fut_short"])
    dates = sorted(p["date"].unique())
    out = {"d": dates}
    for who in ("FII", "DII", "Pro", "Client"):
        w = p[p["who"] == who].set_index("date").reindex(dates)
        out[who] = {k: [None if pd.isna(v) else _num(v, 1 if k == "fut_long_pct" else 0) for v in w[k]]
                    for k in ("fut_long", "fut_short", "fut_net", "call_net", "put_net", "fut_long_pct")}
    return out


def build_options(fo, part, vix, ohlcv=None, intraday=None):
    """fo/part may be None when NSE blocked the download; returns None so the tab shows a clear message."""
    if fo is None or fo.empty:
        return None
    symbols = {s: b for s in ("NIFTY", "BANKNIFTY") if (b := symbol_block(fo, s, ohlcv=(ohlcv or {}).get(s), intraday=(intraday or {}).get(s))) is not None}
    if not symbols:
        return None
    v = None
    if vix is not None and len(vix):
        v = vix.dropna().tail(780)
        v = {"d": [d.strftime("%Y-%m-%d") for d in v.index], "v": [_num(x) for x in v.values]}
    return {"symbols": symbols, "part": participant_block(part), "vix": v}
