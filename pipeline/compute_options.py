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


def symbol_block(fo: pd.DataFrame, sym: str, max_expiries=4, band=0.10):
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
            "chain": {"asof": last.strftime("%Y-%m-%d"), "spot": _num(s0), "expiries": out_exp}, "gex": gex}


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


def build_options(fo, part, vix):
    """fo/part may be None when NSE blocked the download; returns None so the tab shows a clear message."""
    if fo is None or fo.empty:
        return None
    symbols = {s: b for s in ("NIFTY", "BANKNIFTY") if (b := symbol_block(fo, s)) is not None}
    if not symbols:
        return None
    v = None
    if vix is not None and len(vix):
        v = vix.dropna().tail(780)
        v = {"d": [d.strftime("%Y-%m-%d") for d in v.index], "v": [_num(x) for x in v.values]}
    return {"symbols": symbols, "part": participant_block(part), "vix": v}
