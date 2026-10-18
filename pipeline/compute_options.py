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


def volume_profile(df, window, bin_size, value_pct=0.70):
    """Composite volume profile from DAILY bars (not intraday): each day's volume is spread evenly over its high-low range.
    POC = price bin with the most volume; the value area is the smallest band around it holding `value_pct` of the volume
    (VAH / VAL = its upper / lower edge). df columns: h (high), l (low), v (volume), indexed by date."""
    d = df.dropna(subset=["h", "l", "v"]).tail(window)
    d = d[d["v"] > 0]
    if len(d) < min(window, 3):
        return None
    lo = np.floor(d["l"].min() / bin_size) * bin_size
    hi = max(np.ceil(d["h"].max() / bin_size) * bin_size, lo + bin_size)       # at least one bin even if every day is flat
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
    inc, acc, up, dn = {poc}, vol[poc], poc + 1, poc - 1
    while acc < value_pct * tot:
        vu = vol[up] if up < n else -1.0
        vd = vol[dn] if dn >= 0 else -1.0
        if vu < 0 and vd < 0:
            break
        if vu >= vd:
            inc.add(up); acc += vu; up += 1
        else:
            inc.add(dn); acc += vd; dn -= 1
    return {"w": window, "days": int(len(d)), "from": d.index[0].strftime("%Y-%m-%d"), "to": d.index[-1].strftime("%Y-%m-%d"),
            "poc": _num((edges[poc] + edges[poc + 1]) / 2, 0), "vah": _num(edges[max(inc) + 1], 0), "val": _num(edges[min(inc)], 0),
            "share": round(100 * acc / tot)}


def symbol_block(fo: pd.DataFrame, sym: str, max_expiries=4, band=0.10, ohlcv=None):
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
    return {"hist": {"d": hist_d, "pcr_oi": pcr_oi, "pcr_vol": pcr_vol, "und": und},
            "chain": {"asof": last.strftime("%Y-%m-%d"), "spot": _num(s0), "expiries": out_exp}, "gex": gex,
            "vp": {str(w): volume_profile(ohlcv, w, 25 if sym == "NIFTY" else 50) for w in (5, 10, 20, 60)} if ohlcv is not None else None}


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


def build_options(fo, part, vix, ohlcv=None):
    """fo/part may be None when NSE blocked the download; returns None so the tab shows a clear message."""
    if fo is None or fo.empty:
        return None
    symbols = {s: b for s in ("NIFTY", "BANKNIFTY") if (b := symbol_block(fo, s, ohlcv=(ohlcv or {}).get(s))) is not None}
    if not symbols:
        return None
    v = None
    if vix is not None and len(vix):
        v = vix.dropna().tail(780)
        v = {"d": [d.strftime("%Y-%m-%d") for d in v.index], "v": [_num(x) for x in v.values]}
    return {"symbols": symbols, "part": participant_block(part), "vix": v}
