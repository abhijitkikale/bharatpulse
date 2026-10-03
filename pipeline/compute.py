"""Turn raw fetched data into the JSON payload embedded in dashboard.html. Pure functions where possible (see tests/)."""
import math
import pandas as pd
from config import *

OFFSETS = {"1W": pd.DateOffset(days=7), "1M": pd.DateOffset(months=1), "3M": pd.DateOffset(months=3),
           "6M": pd.DateOffset(months=6), "1Y": pd.DateOffset(years=1), "3Y": pd.DateOffset(years=3),
           "5Y": pd.DateOffset(years=5)}
YEARS = {"3Y": 3, "5Y": 5}


def _num(x, nd=2):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def period_returns(s: pd.Series, asof=None, tolerance_days=6):
    """% return per period from a date-indexed close series. 3Y/5Y are annualised. None if history is too short."""
    s = s.dropna()
    out = {p: None for p in PERIODS}
    if s.empty:
        return out
    asof = asof or s.index[-1]
    s = s[s.index <= asof]
    if s.empty:
        return out
    last = s.iloc[-1]
    for p, off in OFFSETS.items():
        target = asof - off
        if s.index[0] > target + pd.Timedelta(days=tolerance_days):
            continue
        before = s[s.index <= target]
        base = before.iloc[-1] if len(before) else s.iloc[0]
        r = last / base
        if p in YEARS:
            r = r ** (1 / YEARS[p])
        out[p] = _num((r - 1) * 100)
    return out


def trend_flags(close: pd.Series):
    """SMA-based flags used by the strength/weakness and breadth panels."""
    c = close.dropna()
    if len(c) < 50:
        return None
    p = c.iloc[-1]
    s25, s50 = c.rolling(25).mean().iloc[-1], c.rolling(50).mean().iloc[-1]
    s200 = c.rolling(200).mean().iloc[-1] if len(c) >= 200 else float("nan")
    ok200 = not math.isnan(s200)
    return {
        "sma50": _num(s50), "sma200": _num(s200) if ok200 else None,
        "a50": bool(p > s50), "a200": bool(p > s200) if ok200 else None,
        "gc": bool(s50 > s200) if ok200 else None,                       # golden cross state (50 > 200)
        "bs": bool(p > s25 > s50 > s200) if ok200 else None,             # brutal strength
        "bw": bool(p < s25 < s50 < s200) if ok200 else None,             # brutal weakness
    }


def build_indices(allx):
    piv = allx.pivot_table(index="date", columns="Index Name", values="Closing Index Value").sort_index()
    pe = allx.pivot_table(index="date", columns="Index Name", values="P/E").sort_index()
    pb = allx.pivot_table(index="date", columns="Index Name", values="P/B").sort_index()
    dy = allx.pivot_table(index="date", columns="Index Name", values="Div Yield").sort_index()
    asof = piv.index.max()
    out = {}
    for group, table in (("benchmark", BENCHMARKS), ("sector", SECTORS)):
        for label, (nse, csv) in table.items():
            if nse not in piv.columns:
                continue
            s = piv[nse].dropna()
            wk = s.resample("W-FRI").last().dropna().tail(262)
            step = 1 if group == "benchmark" else 5
            pes = pe[nse].dropna().iloc[::-1].iloc[::step].iloc[::-1]
            last = lambda df: _num(df[nse].dropna().iloc[-1]) if df[nse].notna().any() else None
            out[label] = {
                "label": label, "group": group, "nse": nse, "csv": csv,
                "close": _num(s.iloc[-1]), "chg": _num((s.iloc[-1] / s.iloc[-2] - 1) * 100) if len(s) > 1 else None,
                "ret": period_returns(s, asof), "pe": last(pe), "pb": last(pb), "dy": last(dy),
                "wk": {"d": [d.strftime("%Y-%m-%d") for d in wk.index], "c": [_num(v) for v in wk.values]},
                "pes": {"d": [d.strftime("%Y-%m-%d") for d in pes.index], "pe": [_num(v) for v in pes.values],
                        "pb": [_num(v) for v in pb[nse].reindex(pes.index).values],
                        "dy": [_num(v) for v in dy[nse].reindex(pes.index).values]},
            }
    return out, asof, piv


def build_global(gpx, piv):
    usd_per = lambda sym, kind: (gpx[sym] if kind == "usd_per" else 1 / gpx[sym])   # USD per 1 local unit
    series = {}
    for label, (sym, f) in GLOBAL.items():
        if sym in gpx.columns:
            series[label] = (gpx[sym], usd_per(*f) if f else None)
    inr = usd_per(*INR_FX)
    for label, nse in (("Nifty 50 (India)", "Nifty 50"), ("NIFTY 500 (India)", "Nifty 500")):
        series[label] = (piv[nse], inr)
    out = []
    for label, (local, rate) in series.items():
        loc = period_returns(local)
        usd = period_returns(local * rate.reindex(local.index, method="ffill")) if rate is not None else loc
        out.append({"name": label, "local": loc, "usd": usd})
    return out


def build_stocks(cons, px, mcap, indices):
    """Per-stock facts + membership. Universe = Nifty 500 + Microcap 250."""
    uni = pd.concat([cons[f] for f in UNIVERSE_FILES]).drop_duplicates("Symbol").set_index("Symbol")
    members = {}
    for label, meta in indices.items():
        if meta["csv"] and meta["csv"] in cons:
            members[label] = list(cons[meta["csv"]]["Symbol"])
    members["Private Bank"] = [s for s in members.get("Bank", []) if s not in set(members.get("PSU Bank", []))]
    members["Capital Market"] = [s for s in CAPITAL_MARKET_SYMBOLS if s in uni.index]
    asof = px.index.max()
    dates = list(px.index[-260:])
    stocks = {}
    for sym, row in uni.iterrows():
        col = sym + ".NS"
        if col not in px.columns:
            continue
        c = px[col].dropna()
        if len(c) < 20 or (asof - c.index[-1]).days > 7:
            continue
        flags = trend_flags(c) or {}
        hi, lo = c.tail(252).max(), c.tail(252).min()
        stocks[sym] = {
            "s": sym, "n": row["Company Name"], "i": row["Industry"], "p": _num(c.iloc[-1]),
            "d1": _num((c.iloc[-1] / c.iloc[-2] - 1) * 100), "r": period_returns(c, asof), **flags,
            "pos": _num((c.iloc[-1] - lo) / (hi - lo) * 100, 0) if hi > lo else None,
            "hi": _num(hi), "lo": _num(lo),
            "mc": _num(mcap.get(col, {}).get("v", 0) / 1e7, 0) or None,           # Rs crore
            "mck": mcap.get(col, {}).get("kind"),
            "ser": [_num(v) for v in c.reindex(dates).values],
            "s50": [_num(v) for v in c.rolling(50).mean().reindex(dates).values],
            "s200": [_num(v) for v in c.rolling(200).mean().reindex(dates).values],
        }
    ranked = sorted((s for s in stocks.values() if s["mc"]), key=lambda s: -s["mc"])
    for i, s in enumerate(ranked, 1):
        s["rk"] = i
    inv = {}
    for label in list(members):
        members[label] = [s for s in members[label] if s in stocks]
        for s in members[label]:
            inv.setdefault(s, []).append(label)
    for s, st in stocks.items():
        st["x"] = inv.get(s, [])
    return list(stocks.values()), members, [d.strftime("%Y-%m-%d") for d in dates]


def assemble(allx, cons, px, mcap, gpx, fii, status):
    indices, asof, piv = build_indices(allx)
    stocks, members, dates = build_stocks(cons, px, mcap, indices)
    return {
        "asof": asof.strftime("%d %b %Y"), "stock_asof": px.index.max().strftime("%d %b %Y"),
        "periods": PERIODS, "indices": list(indices.values()), "members": members,
        "stocks": stocks, "dates": dates,
        "global": build_global(gpx, piv) if gpx is not None else [],
        "flows": fii.dropna(how="all").to_dict("records"),
        "status": status,
        "mcap_note": "Free-float mcap (Yahoo float shares x price); total mcap where float is unavailable",
    }
