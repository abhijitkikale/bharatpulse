"""The day's summary for the 30-second snap video (tools/make_snap.py). Pure functions: the dashboard data dict D (the `const D = {...}` of the published page,
or data/dashboard.json) in, a small JSON-able summary out. The wording is factual and never says buy / sell / target.

Sections: headline, indices, breadth, India VIX, sectors (best / worst), momentum leaders (a Python copy of the Momentum Stocks scoring: non-overlapping
periods, latest week skipped, 6M horizon, 0.6 sector / 0.4 stock), "unusual" insights (scored, the strongest one or two are shown), and tomorrow's levels."""
import datetime as dt

import stream_levels

HORIZON, WS, WT = "6M", 0.6, 0.4
# one timeline shared by the page and the video maker: (scene id, name, start s, end s); scenes overlap a little for the fades
TIMELINE = [("s1", "Hook", 0.0, 4.3), ("s2", "Indices", 3.9, 9.9), ("s3", "Breadth, volatility, valuation", 9.5, 15.9), ("s4", "Sector heat map", 15.5, 22.4),
            ("s5", "Nifty 50 movers", 22.0, 27.9), ("s6", "Momentum leaders", 27.5, 34.9), ("s7", "Flows and global markets", 34.5, 41.4), ("s8", "Something unusual", 41.0, 46.4),
            ("s9", "Levels to watch", 46.0, 52.4), ("s10", "Dashboard link", 52.0, 56.0)]
DUR = 56.0
SPREV = {"1M": "1W", "3M": "1M", "6M": "3M", "1Y": "6M", "3Y": "1Y", "5Y": "3Y"}
SYRS = {"3Y": 3, "5Y": 5}


# ---------------- momentum (a copy of the dashboard's Momentum Stocks scoring) ----------------
def _total(r, q):
    return (1 + r / 100) ** SYRS.get(q, 1)


def _slice_get(get):
    def g(it, q):
        a = get(it, q)
        if a is None or q not in SPREV:
            return a
        b = get(it, SPREV[q])
        return None if b is None else (_total(a, q) / _total(b, SPREV[q]) - 1) * 100
    return g


def _ranks(items, get, periods):
    out = {}
    for p in periods:
        v = sorted(((it, get(it, p)) for it in items if get(it, p) is not None), key=lambda x: -x[1])
        for k, (it, _) in enumerate(v):
            out.setdefault(it["key"], {})[p] = (k + 1, len(v))
    return out


def _composite(items, get, periods, inc):
    rc = _ranks(items, _slice_get(get), periods)
    rows = []
    for it in items:
        rs = [(rc.get(it["key"], {}) or {}).get(q) for q in inc]
        ok = all(rs)
        rows.append({"key": it["key"], "ok": ok, "rs": [r[0] for r in rs] if ok else None, "sum": sum(r[0] for r in rs) if ok else None})
    rows.sort(key=lambda c: (0, c["sum"], c["rs"], c["key"]) if c["ok"] else (1, 0, [], c["key"]))
    n_ok = 0
    for c in rows:
        if c["ok"]:
            n_ok += 1
            c["rank"] = n_ok
        else:
            c["rank"] = None
    return {c["key"]: c for c in rows}, n_ok


def _pct0(rank, n):
    return (rank - 1) / (n - 1) * 100 if n > 1 else 0.0


def sector_composite(D, horizon=HORIZON):
    periods = [p for p in D["periods"] if p != "1D"]
    inc = [q for q in periods[:periods.index(horizon) + 1] if q != "1W"]
    sect = [{"key": i["label"], "i": i} for i in D["indices"] if i["group"] == "sector"]
    cr, n = _composite(sect, lambda it, p: it["i"]["ret"].get(p), D["periods"], inc)
    return cr, n, inc


def momentum_rows(D, horizon=HORIZON, ws=WS, wt=WT):
    """All stocks with their weighted momentum score (lowest = strongest), sorted like the dashboard's Momentum Stocks tab."""
    stk = {s["s"]: s for s in D["stocks"]}
    cr, n_sec, inc = sector_composite(D, horizon)
    sect_set = {i["label"] for i in D["indices"] if i["group"] == "sector"}
    assigned = {}
    for s in D["stocks"]:
        c = [l for l in (s.get("x") or []) if l in sect_set and cr.get(l, {}).get("ok")]
        if not c:
            continue
        c.sort(key=lambda l: (len(D["members"][l]), cr[l]["rank"]))
        assigned.setdefault(c[0], []).append(s)
    rows = []
    for sec, lst in assigned.items():
        mem = [{"key": s["s"], "s": s} for s in (stk.get(m) for m in D["members"][sec]) if s]
        W, n_in = _composite(mem, lambda it, p: it["s"]["r"].get(p), D["periods"], inc)
        s_pct = _pct0(cr[sec]["rank"], n_sec)
        for s in lst:
            c = W.get(s["s"])
            if not c or not c["ok"]:
                continue
            w = round((ws * s_pct + wt * _pct0(c["rank"], n_in)) * 1e4) / 1e4
            rows.append({"s": s["s"], "n": s["n"], "sector": sec, "w": w, "ret": s["r"].get(horizon), "d1": s.get("d1"), "r1m": s["r"].get("1M"), "p": s.get("p")})
    rows.sort(key=lambda r: (r["w"], -(r["ret"] if r["ret"] is not None else -1e9)))
    for k, r in enumerate(rows):
        r["rank"] = k + 1
    return rows


# ---------------- insights ----------------
def _idx(D, label):
    return next((i for i in D["indices"] if i["label"] == label), None)


def _flow_streak(flows, key):
    """Consecutive most-recent days with the same sign for FII / DII."""
    if not flows:
        return 0, 0.0
    last = flows[-1][key]
    n = 0
    for f in reversed(flows):
        if (f[key] > 0) == (last > 0) and f[key] != 0:
            n += 1
        else:
            break
    return n, last


def _ordinal(n):
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def insights(D, nifty, pct_adv, sectors, snap_prev=None):
    """Candidate 'unusual' items, strongest first: [{kind, score, title, line, sub}]. Plain facts only."""
    out = []
    chg = nifty["chg"]
    # breadth against the index
    if chg >= 0.3 and pct_adv <= 45:
        out.append({"kind": "breadth", "score": 80 + (45 - pct_adv), "title": "BREADTH", "line": f"Nifty up {chg:.1f}%, but only {pct_adv:.0f}% of stocks advanced", "sub": "a narrow rally"})
    elif chg <= -0.3 and pct_adv >= 55:
        out.append({"kind": "breadth", "score": 80 + (pct_adv - 55), "title": "BREADTH", "line": f"Nifty down {abs(chg):.1f}%, yet {pct_adv:.0f}% of stocks advanced", "sub": "weakness is concentrated"})
    elif pct_adv >= 75:
        out.append({"kind": "breadth", "score": 50 + (pct_adv - 75), "title": "BREADTH", "line": f"Broad rally: {pct_adv:.0f}% of stocks advanced", "sub": ""})
    elif pct_adv <= 25:
        out.append({"kind": "breadth", "score": 50 + (25 - pct_adv), "title": "BREADTH", "line": f"Broad sell-off: only {pct_adv:.0f}% of stocks advanced", "sub": ""})
    # smallcaps against the Nifty
    sm = _idx(D, "Nifty Smallcap 250")
    if sm and abs(sm["chg"] - chg) >= 1.0:
        gap = sm["chg"] - chg
        out.append({"kind": "size", "score": 40 + 20 * abs(gap), "title": "SMALLCAPS", "line": f"Smallcaps {'beat' if gap > 0 else 'lagged'} the Nifty by {abs(gap):.1f} points", "sub": f"Smallcap 250 {sm['chg']:+.1f}%, Nifty {chg:+.1f}%"})
    # a sector far from the Nifty
    for s in (sectors["best"][:1] + sectors["worst"][:1]):
        d = s["chg"] - chg
        if abs(d) >= 2.0:
            out.append({"kind": "sector", "score": 45 + 10 * abs(d), "title": "SECTOR", "line": f"{s['name']} {s['chg']:+.1f}% against Nifty {chg:+.1f}%", "sub": f"{abs(d):.1f} points {'ahead of' if d > 0 else 'behind'} the index"})
    # foreign / domestic institutions
    flows = D.get("flows") or []
    if flows:
        n, fii = _flow_streak(flows, "FII")
        _, dii = _flow_streak(flows, "DII")
        if abs(fii) >= 3000 or n >= 4:
            word = "bought" if fii > 0 else "sold"
            line = f"FIIs {word} ₹{abs(fii):,.0f} cr" + (f", {_ordinal(n)} day running" if n >= 2 else "")
            out.append({"kind": "flows", "score": 30 + abs(fii) / 200 + 5 * n, "title": "FLOWS", "line": line, "sub": f"DIIs {'bought' if dii > 0 else 'sold'} ₹{abs(dii):,.0f} cr" if dii else ""})
    # India VIX
    v = (D.get("options") or {}).get("vix") or {}
    if v.get("v") and len(v["v"]) >= 2 and v["v"][-2]:
        pc = (v["v"][-1] / v["v"][-2] - 1) * 100
        if abs(pc) >= 5 or v["v"][-1] >= 20 or v["v"][-1] <= 12:
            out.append({"kind": "vix", "score": 35 + 2 * abs(pc), "title": "VOLATILITY", "line": f"India VIX {'fell' if pc < 0 else 'rose'} {abs(pc):.1f}% to {v['v'][-1]:.1f}", "sub": ""})
    # a big move on a very large volume
    for sp in (D.get("activity") or {}).get("spikes", []):
        if abs(sp["chg"]) >= 8 and sp["x"] >= 10 and sp.get("tcr", 0) >= 100:
            out.append({"kind": "volume", "score": 40 + abs(sp["chg"]) + min(sp["x"], 60) / 3, "title": "VOLUME", "line": f"{sp['s']} {sp['chg']:+.1f}% on {sp['x']:.0f}x its usual volume", "sub": f"turnover ₹{sp['tcr']:,.0f} cr"})
            break
    # gamma regime against the flip
    try:
        lv = stream_levels.eod_levels(D, "NIFTY")
        flip, spot = lv["levels"].get("Gamma flip"), lv["levels"].get("Spot")
        if flip and spot:
            dist = (spot / flip - 1) * 100
            pos = "positive" if lv["positive"] else "negative"
            if abs(dist) <= 0.4:
                out.append({"kind": "gamma", "score": 55, "title": "GAMMA", "line": f"Nifty closed within {abs(dist):.1f}% of the gamma flip ({flip:,.0f})", "sub": f"{pos} gamma regime, a small move can switch it"})
            else:
                out.append({"kind": "gamma", "score": 30, "title": "GAMMA", "line": f"Nifty closed {'above' if dist > 0 else 'below'} the gamma flip ({flip:,.0f})", "sub": f"{pos} gamma regime"})
            if snap_prev and snap_prev.get("regime") and snap_prev["regime"] != pos.upper():
                out.append({"kind": "gamma", "score": 85, "title": "GAMMA", "line": f"Gamma regime flipped to {pos}", "sub": f"it was {snap_prev['regime'].lower()} yesterday"})
    except Exception:
        pass
    # 52-week highs / lows
    hi = sum(1 for s in D["stocks"] if s.get("hi") and s.get("p") and s["p"] >= 0.99 * s["hi"])
    lo = sum(1 for s in D["stocks"] if s.get("lo") and s.get("p") and s["p"] <= 1.01 * s["lo"])
    if hi >= 60:
        out.append({"kind": "highs", "score": 35 + hi / 5, "title": "52-WEEK HIGHS", "line": f"{hi} stocks closed within 1% of their 52-week high", "sub": ""})
    if lo >= 60:
        out.append({"kind": "lows", "score": 35 + lo / 5, "title": "52-WEEK LOWS", "line": f"{lo} stocks closed within 1% of their 52-week low", "sub": ""})
    # sector composite rank moves (needs yesterday's snapshot)
    if snap_prev and snap_prev.get("sector_rank"):
        cr, _, _ = sector_composite(D)
        mv = [(snap_prev["sector_rank"].get(k) - c["rank"], k, c["rank"]) for k, c in cr.items() if c["ok"] and snap_prev["sector_rank"].get(k)]
        if mv:
            d, k, r = max(mv, key=lambda x: abs(x[0]))
            if abs(d) >= 4:
                out.append({"kind": "rank", "score": 50 + 5 * abs(d), "title": "ROTATION", "line": f"{k} moved {'up' if d > 0 else 'down'} {abs(d)} places in the sector momentum ranking", "sub": f"now #{r}"})
    out.sort(key=lambda x: -x["score"])
    return out


# ---------------- the day's summary ----------------
def _fmt_date(D):
    d = (D.get("dates") or [None])[-1]
    return dt.date.fromisoformat(d) if d else dt.datetime.strptime(D["asof"], "%d %b %Y").date()


def _short(name):
    return name.split(" (")[0]


def extras(D, nifty):
    """The series for the charts. Every part is optional: a missing series only means that chart is left out."""
    out = {}
    try:
        wk = nifty["wk"]
        out["nifty_weekly"] = {"d": wk["d"][-52:], "c": wk["c"][-52:]}
    except Exception:
        pass
    try:
        out["index_returns"] = {i["label"]: {p: i["ret"].get(p) for p in ("1D", "1W", "1M", "3M", "6M", "1Y")} for i in D["indices"] if i["group"] == "benchmark" or i["label"] in ("Bank", "Nifty Bank")}
    except Exception:
        pass
    try:
        b = D["activity"]["breadth"]
        out["breadth_hist"] = [{"d": d, "adv": a, "dec": c} for d, a, c in zip(b["d"][-25:], b["adv"][-25:], b["dec"][-25:])]
    except Exception:
        pass
    try:
        v = D["options"]["vix"]
        out["vix_hist"] = {"d": v["d"][-60:], "v": v["v"][-60:]}
    except Exception:
        pass
    try:
        pes = nifty["pes"]["pe"]
        out["pe"] = {"now": nifty["pe"], "pb": nifty.get("pb"), "dy": nifty.get("dy"), "min": min(pes), "max": max(pes), "pct": round(sum(1 for x in pes if x <= nifty["pe"]) / len(pes) * 100)}
    except Exception:
        pass
    out["heat"] = sorted(({"name": i["label"], "chg": i["chg"]} for i in D["indices"] if i["group"] == "sector"), key=lambda x: -x["chg"])
    try:
        stk = {s["s"]: s for s in D["stocks"]}
        mem = [stk[m] for m in D["members"]["Nifty 50"] if m in stk and stk[m].get("d1") is not None]
        mem.sort(key=lambda x: -x["d1"])
        out["n50"] = {"up": sum(1 for x in mem if x["d1"] > 0), "n": len(mem), "gainers": [{"s": x["s"], "d1": x["d1"]} for x in mem[:4]], "losers": [{"s": x["s"], "d1": x["d1"]} for x in reversed(mem[-4:])]}
    except Exception:
        pass
    try:
        out["flows_hist"] = [{"d": f["date"], "FII": f["FII"], "DII": f["DII"]} for f in D["flows"][-10:]]
    except Exception:
        pass
    try:
        want = ["S&P 500", "NASDAQ", "Dow Jones", "FTSE 100", "DAX", "Nikkei 225", "Hang Seng", "KOSPI", "ASX 200"]
        g = {_short(x["name"]): x["local"].get("1D") for x in D["global"]}
        out["global"] = [{"name": n, "chg": g[n]} for n in want if g.get(n)][:8]
    except Exception:
        pass
    try:
        o = D["options"]["symbols"]["NIFTY"]
        gx, spot = o["gex"]["nearest"], o["chain"]["spot"]
        pr = [(a, b) for a, b in zip(gx["profile"]["s"], gx["profile"]["g"]) if abs(a / spot - 1) <= 0.035]
        out["gex"] = {"s": [a for a, _ in pr], "g": [b for _, b in pr], "flip": gx["flip"], "spot": spot, "call": gx["walls"].get("call_gex"), "put": gx["walls"].get("put_gex")}
    except Exception:
        pass
    return out


def build(D, snap_prev=None):
    day = _fmt_date(D)
    nifty = _idx(D, "Nifty 50")
    bank = next((i for i in D["indices"] if i["label"] in ("Bank", "Nifty Bank")), None)
    names = [("Nifty 50", nifty), ("Bank Nifty", bank), ("Midcap 150", _idx(D, "Nifty Midcap 150")), ("Smallcap 250", _idx(D, "Nifty Smallcap 250"))]
    indices = [{"name": n, "close": i["close"], "chg": i["chg"], "ret": {p: i["ret"].get(p) for p in ("1D", "1W", "1M", "3M", "6M", "1Y")}} for n, i in names if i]
    b = (D.get("activity") or {}).get("breadth") or {}
    adv, dec = (b["adv"][-1], b["dec"][-1]) if b.get("adv") else (0, 0)
    pct_adv = adv / (adv + dec) * 100 if adv + dec else 50.0
    v = (D.get("options") or {}).get("vix") or {}
    vix = {"value": v["v"][-1], "chg": round((v["v"][-1] / v["v"][-2] - 1) * 100, 1)} if v.get("v") and len(v["v"]) >= 2 else None
    sect = sorted(({"name": i["label"], "chg": i["chg"], "week": i["ret"].get("1W")} for i in D["indices"] if i["group"] == "sector"), key=lambda x: -x["chg"])
    sectors = {"best": sect[:3], "worst": list(reversed(sect[-3:]))}
    mom = momentum_rows(D)
    stk_by = {s["s"]: s for s in D["stocks"]}
    leaders = []
    for r in mom[:3]:
        st = stk_by[r["s"]]
        leaders.append({**{k: r[k] for k in ("s", "n", "sector", "d1", "r1m", "ret", "p")}, "r3m": st["r"].get("3M"), "r6m": st["r"].get("6M"), "ser": [round(x, 2) for x in (st.get("ser") or [])[-120:]]})
    ins = insights(D, nifty, pct_adv, sectors, snap_prev)
    levels = None
    try:
        lv = stream_levels.eod_levels(D, "NIFTY")
        L = lv["levels"]
        levels = {"spot": L.get("Spot"), "regime": "POSITIVE" if lv["positive"] else "NEGATIVE" if lv["positive"] is not None else None, "flip": L.get("Gamma flip"),
                  "call_wall": L.get("GEX call wall"), "put_wall": L.get("GEX put wall"), "max_pain": L.get("Max pain"), "oi_call": L.get("OI call wall"), "oi_put": L.get("OI put wall")}
    except Exception:
        pass
    hook_sub = ins[0]["line"] if ins else f"{adv:,} stocks up, {dec:,} down"
    snap = {"date": str(day), "label": day.strftime("%d %b %Y").lstrip("0"), "weekday": day.strftime("%A"),
            "hook": {"title": "Nifty 50", "close": nifty["close"], "chg": nifty["chg"], "sub": hook_sub},
            "indices": indices, "breadth": {"adv": adv, "dec": dec, "pct_adv": round(pct_adv, 1)}, "vix": vix, "sectors": sectors, "leaders": leaders,
            "insights": ins[:3], "all_insights": ins[:6], "levels": levels, "link": "abhijitkikale.github.io/bharatpulse",
            "duration": DUR, "timeline": TIMELINE, **extras(D, nifty)}
    return snap


def snapshot_for_history(D, snap):
    """The small record kept each day so the next day's video can say what changed."""
    cr, _, _ = sector_composite(D)
    return {"date": snap["date"], "sector_rank": {k: c["rank"] for k, c in cr.items() if c["ok"]}, "momentum_top": [r["s"] for r in momentum_rows(D)[:10]],
            "regime": (snap["levels"] or {}).get("regime"), "vix": (snap["vix"] or {}).get("value"), "pct_adv": snap["breadth"]["pct_adv"]}


def caption(snap):
    """Title, description, hashtags and disclaimer for the upload."""
    h = snap["hook"]
    title = f"Nifty {h['chg']:+.2f}% | Sectors, momentum and what's unusual | {snap['label']}"
    lines = [f"{snap['weekday']} {snap['label']}: Nifty 50 closed at {h['close']:,.0f} ({h['chg']:+.2f}%).",
             f"Breadth: {snap['breadth']['adv']:,} advancing, {snap['breadth']['dec']:,} declining."]
    if snap["sectors"]["best"]:
        s = snap["sectors"]
        lines.append("Best sectors: " + ", ".join(f"{x['name']} {x['chg']:+.1f}%" for x in s["best"]) + ". Weakest: " + ", ".join(f"{x['name']} {x['chg']:+.1f}%" for x in s["worst"]) + ".")
    for i in snap["insights"]:
        lines.append(f"{i['title'].title()}: {i['line']}.")
    lines.append(f"Full dashboard: https://{snap['link']}/")
    lines.append("Not SEBI registered. This is not investment advice or a recommendation to buy or sell anything. Data for education only; it may be delayed or wrong.")
    tags = "#Nifty #Nifty50 #StockMarketIndia #Sensex #BankNifty #MarketUpdate #Sectors #Momentum #GEX #OptionsData #DailyMarketWrap"
    return title, "\n".join(lines), tags
