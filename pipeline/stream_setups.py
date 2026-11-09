"""CALL / PUT setups for the stream page: the same rules as tradingview/BharatPulse-Levels-live.pine, replayed over today's 1-minute candles.

* touch > doji > retest at a level, the NEXT candle decides CALL (closes above) / PUT (closes below), tagged bounce / cross.
  Two copies of the stage machine run side by side (original rules, and rule A: a candle closing through the level counts as step 2,
  rule B: a doji counts within ZONE points of the level), so A / B only add setups.
* VWAP break: the previous LOOK candles all on one side of the VWAP, a candle closes through it, the next candle closes beyond it.
* VWAP bounce: the previous BOUNCE_LOOK candles all on one side of the VWAP, a candle touches it and closes back on that side, the next candle
  closes beyond it; at most one bounce signal per BOUNCE_COOL candles on the same VWAP.
Levels that may give setups: end-of-day GEX call / put wall, POC, VAH, VAL, weekly POC / VAH / VAL; today's developing daily and weekly
VWAP, Now VAH and Now VAL. Pure functions, no network."""
import numpy as np
import pandas as pd

import compute_options

TOL, DOJI, MAXWAIT, MINAWAY, ZONE, LOOK = 0.03, 30.0, 12, 1, 12.0, 8
BOUNCE_LOOK, BOUNCE_COOL = 5, 10          # VWAP bounce: candles before the touch on one side, and candles between two bounce signals
WARMUP = (9, 45)             # Now VAH / Now VAL come from a profile of a few candles before this time: no setups on them yet
SETUP_EOD = ("GEX call wall", "GEX put wall", "POC", "VAH", "VAL", "Weekly POC", "Weekly VAH", "Weekly VAL")
NOW_VA_SETUPS = False
STRIKE = {"NIFTY": 50, "BANKNIFTY": 100}


def developing_va(df, rows=0.25):
    """Per candle: (POC, VAH, VAL) of the volume profile of the session so far (df = one session of 1-minute candles)."""
    base = np.floor(df["l"].min() / rows) * rows
    n = int(np.ceil((df["h"].max() - base) / rows)) + 1
    vol = np.zeros(n)
    out = []
    last = (np.nan, np.nan, np.nan)
    for r in df.itertuples():
        v = float(r.v) if r.v == r.v else 0.0
        if v > 0:
            a, b = float(r.l), float(r.h)
            i0 = int((a - base) // rows)
            if b - a < 1e-9:
                vol[min(i0, n - 1)] += v
            else:
                i1 = min(int((b - base) // rows), n - 1)
                edges = base + np.arange(i0, i1 + 2) * rows
                ov = np.clip(np.minimum(b, edges[1:]) - np.maximum(a, edges[:-1]), 0, None)
                vol[i0:i1 + 1] += v * ov / (b - a)
            poc = int(vol.argmax())
            lo, hi, _ = compute_options._value_area(vol, poc, 0.70)
            last = (base + (poc + 0.5) * rows, base + (hi + 1) * rows, base + lo * rows)
        out.append(last)
    return out


def find_setups(df, eod_levels, daily, weekly, va, sym="NIFTY"):
    """df: today's 1-minute candles. eod_levels: {name: value}. daily / weekly: VWAP series. va: developing_va(df).
    Returns the setups in time order: {"idx", "time" (pandas Timestamp), "side", "strike", "level", "tag"}."""
    n = len(df)
    o, h, l, c = (df[k].to_numpy(float) for k in ("o", "h", "l", "c"))
    dv = daily.reindex(df.index).ffill().to_numpy(float)
    wv = weekly.reindex(df.index).ffill().to_numpy(float)
    levels = [(k, np.full(n, float(eod_levels[k]))) for k in SETUP_EOD if k in eod_levels]
    warm = np.array([(ts.hour, ts.minute) < WARMUP for ts in df.index])
    vah, val = np.array([x[1] for x in va], float), np.array([x[2] for x in va], float)
    vah[warm], val[warm] = np.nan, np.nan
    levels += [("Now VWAP", dv), ("Now Weekly VWAP", wv)]
    if NOW_VA_SETUPS:                                             # off by default: the TradingView indicator cannot give these on past candles
        levels += [("Now VAH", vah), ("Now VAL", val)]
    vwap_idx = [i for i, (k, _) in enumerate(levels) if k in ("Now VWAP", "Now Weekly VWAP")]
    step = STRIKE.get(sym, 50)
    stg = [[0] * len(levels) for _ in range(2)]
    sbar = [[0] * len(levels) for _ in range(2)]
    away = [[0] * len(levels) for _ in range(2)]
    pend = [[None] * len(levels) for _ in range(2)]            # (side, bar)
    em_bar = [-1] * len(levels)
    bk = {i: None for i in vwap_idx}                          # VWAP break waiting for its confirmation candle: (side, bar)
    bn = {i: None for i in vwap_idx}                          # VWAP bounce waiting for its confirmation candle
    last_bn = {i: -10 ** 6 for i in vwap_idx}
    out = []

    def emit(n_, i, side, tag):
        em_bar[i] = n_
        out.append({"idx": n_, "time": df.index[n_], "side": side, "strike": int(round(c[n_] / step) * step),
                    "level": f"{levels[i][0]} {levels[i][1][n_]:.2f}", "tag": tag})

    for k in range(n):
        rng = h[k] - l[k]
        doji = rng > 0 and abs(c[k] - o[k]) <= rng * DOJI / 100.0
        for i, (name, series) in enumerate(levels):
            L = series[k]
            if L != L:
                continue
            tl = L * TOL / 100.0
            touching = h[k] >= L - tl and l[k] <= L + tl
            for ph in (0, 1):
                dz = max(tl, ZONE) if ph == 1 else tl
                near = doji and h[k] >= L - dz and l[k] <= L + dz
                breaks = ph == 1 and touching and ((o[k] < L < c[k]) or (o[k] > L > c[k]))
                emit_side, tag = "", ""
                pd_ = pend[ph][i]
                if pd_ is not None:
                    if k == pd_[1] + 1:
                        emit_side = "CALL" if c[k] > L else "PUT" if c[k] < L else ""
                        tag = "" if emit_side == "" or pd_[0] == "EQ" else (" (bounce)" if emit_side == pd_[0] else " (cross)")
                    pend[ph][i] = None
                s = stg[ph][i]
                if s > 0 and k - sbar[ph][i] > MAXWAIT:
                    s = 0
                    stg[ph][i] = 0
                if s == 0:
                    if touching:
                        if breaks:
                            stg[ph][i] = 2
                            away[ph][i] = 0
                        else:
                            stg[ph][i] = 1
                        sbar[ph][i] = k
                elif s == 1:
                    if (near or breaks) and k > sbar[ph][i]:
                        stg[ph][i] = 2
                        sbar[ph][i] = k
                        away[ph][i] = 0
                elif s == 2:
                    if touching:
                        if away[ph][i] >= MINAWAY:
                            side = "CALL" if c[k] > L else "PUT" if c[k] < L else ""
                            pend[ph][i] = ("EQ" if side == "" else side, k)
                            stg[ph][i] = 0
                    else:
                        away[ph][i] += 1
                if emit_side and em_bar[i] != k:
                    emit(k, i, emit_side, tag)
            if i in bk:                                       # the VWAP break signal
                tlb = tl
                if bk[i] is not None and k == bk[i][1] + 1:
                    ok = c[k] > L + tlb if bk[i][0] == "CALL" else c[k] < L - tlb
                    if ok and em_bar[i] != k:
                        emit(k, i, bk[i][0], " (break)")
                bk[i] = None
                if k >= LOOK:
                    prev = [(c[k - j], series[k - j]) for j in range(1, LOOK + 1)]
                    below = all(a < b for a, b in prev)
                    above = all(a > b for a, b in prev)
                    if c[k] > L and below:
                        bk[i] = ("CALL", k)
                    elif c[k] < L and above:
                        bk[i] = ("PUT", k)
                # the VWAP bounce signal
                if bn[i] is not None and k == bn[i][1] + 1:
                    ok = c[k] > L + tlb if bn[i][0] == "CALL" else c[k] < L - tlb
                    if ok and k - last_bn[i] >= BOUNCE_COOL and em_bar[i] != k:
                        emit(k, i, bn[i][0], " (VWAP bounce)")
                        last_bn[i] = k
                bn[i] = None
                if k >= BOUNCE_LOOK:
                    prev = [(c[k - j], series[k - j]) for j in range(1, BOUNCE_LOOK + 1)]
                    if all(a > b for a, b in prev) and l[k] <= L + tlb and c[k] > L:
                        bn[i] = ("CALL", k)
                    elif all(a < b for a, b in prev) and h[k] >= L - tlb and c[k] < L:
                        bn[i] = ("PUT", k)
    return out
