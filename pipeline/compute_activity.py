"""Market-activity payload: whole-market breadth history, volume leaders and volume spikes (from NSE's daily price file)."""
import math

import pandas as pd

MAINBOARD = {"EQ", "BE", "BZ", "BL", "BT", "IL", "IQ"}      # main-board equity series (SME, bonds, call auction excluded)
SPIKE_X = 2.0                                                # volume this many times the previous 20-day average
MIN_TURNOVER_CR = 5.0                                        # ignore illiquid noise
AVG_DAYS, MIN_AVG_DAYS = 20, 10


def _n(x, nd=2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def _rows(df):
    return [{"s": r.SYMBOL, "p": _n(r.CLOSE_PRICE), "chg": _n(r.chg), "vol": int(r.TTL_TRD_QNTY), "avg": None if pd.isna(r.avg20) else int(r.avg20),
             "x": _n(r.ratio, 1), "tcr": _n(r.turn_cr, 1), "dp": _n(r.DELIV_PER, 0)} for r in df.itertuples()]


def build_activity(bhav, top_n=25, spike_n=30, spike_x=SPIKE_X, min_turnover_cr=MIN_TURNOVER_CR):
    if bhav is None or bhav.empty:
        return None
    m = bhav[bhav["SERIES"].isin(MAINBOARD)].copy()
    for c in ("PREV_CLOSE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "DELIV_PER"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    m = m[(m["PREV_CLOSE"] > 0) & (m["CLOSE_PRICE"] > 0) & m["TTL_TRD_QNTY"].notna()]
    m = m.sort_values("SERIES", key=lambda s: (s != "EQ")).drop_duplicates(["date", "SYMBOL"])      # one row per stock per day, EQ preferred
    m["chg"] = (m["CLOSE_PRICE"] / m["PREV_CLOSE"] - 1) * 100
    m["turn_cr"] = m["TURNOVER_LACS"] / 100.0
    m["adv"] = m["chg"] > 0
    m["dec"] = m["chg"] < 0
    g = m.groupby("date")
    br = pd.DataFrame({"adv": g["adv"].sum(), "dec": g["dec"].sum(), "n": g.size(), "turn": g["turn_cr"].sum()}).sort_index()
    br["unch"] = br["n"] - br["adv"] - br["dec"]
    dates = list(br.index)
    last = dates[-1]
    vol = m.pivot_table(index="date", columns="SYMBOL", values="TTL_TRD_QNTY", aggfunc="first")
    prior = vol[vol.index < last].tail(AVG_DAYS)
    avg = prior.mean().where(prior.count() >= MIN_AVG_DAYS)
    today = m[m["date"] == last].copy()
    today["avg20"] = today["SYMBOL"].map(avg)
    today["ratio"] = today["TTL_TRD_QNTY"] / today["avg20"]
    spikes = today[(today["ratio"] >= spike_x) & (today["turn_cr"] >= min_turnover_cr)].sort_values("ratio", ascending=False).head(spike_n)
    return {
        "asof": last.strftime("%Y-%m-%d"), "n_stocks": int(len(today)),
        "params": {"spike_x": spike_x, "min_turnover_cr": min_turnover_cr, "avg_days": AVG_DAYS},
        "breadth": {"d": [d.strftime("%Y-%m-%d") for d in dates], "adv": [int(v) for v in br["adv"]], "dec": [int(v) for v in br["dec"]],
                    "unch": [int(v) for v in br["unch"]], "turn": [_n(v, 0) for v in br["turn"]]},
        "leaders": {"turnover": _rows(today.sort_values("turn_cr", ascending=False).head(top_n)),
                    "volume": _rows(today.sort_values("TTL_TRD_QNTY", ascending=False).head(top_n))},
        "spikes": _rows(spikes),
    }
