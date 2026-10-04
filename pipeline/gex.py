"""Gamma exposure (GEX), gamma flip and walls from end-of-day option data.

Model (an estimate, not an observation):
  * Black-76 on the forward price; forward per expiry from put-call parity near the money.
  * Implied volatility inverted from each OTM option's end-of-day price (calls above the forward, puts below).
  * Gamma per option from that volatility; GEX = gamma * open interest * spot^2 * 1%, calls positive and puts
    negative - the standard convention that dealers are long calls and short puts.
  * Gamma flip = the spot level where total GEX crosses zero, found by repricing gamma over a grid of spot levels.
"""
import math

import numpy as np

RATE = 0.065                 # approximate Indian short-term risk-free rate
_erf = np.vectorize(math.erf, otypes=[float])
SQRT2 = math.sqrt(2.0)
SQRT2PI = math.sqrt(2.0 * math.pi)


def ncdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / SQRT2))


def npdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / SQRT2PI


def black76(F, K, T, sigma, r, is_call):
    F, K, sigma = (np.asarray(v, dtype=float) for v in (F, K, sigma))
    sd = sigma * math.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    df = math.exp(-r * T)
    call = df * (F * ncdf(d1) - K * ncdf(d2))
    put = df * (K * ncdf(-d2) - F * ncdf(-d1))
    return np.where(is_call, call, put)


def implied_vol(price, F, K, T, r, is_call, lo=0.01, hi=3.0, iters=60):
    """Vectorised bisection; returns NaN where the price is outside the no-arbitrage range."""
    price, F, K, is_call = (np.asarray(v) for v in (price, F, K, is_call))
    price = price.astype(float)
    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    pa = black76(F, K, T, a, r, is_call)
    pb = black76(F, K, T, b, r, is_call)
    ok = (price > pa) & (price < pb)
    for _ in range(iters):
        m = 0.5 * (a + b)
        pm = black76(F, K, T, m, r, is_call)
        up = pm < price
        a = np.where(up, m, a)
        b = np.where(up, b, m)
    return np.where(ok, 0.5 * (a + b), np.nan)


def gamma76(F, K, T, sigma, r):
    """d(delta)/dF for a Black-76 option (same for calls and puts)."""
    F, K, sigma = (np.asarray(v, dtype=float) for v in (F, K, sigma))
    sd = sigma * math.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    return math.exp(-r * T) * npdf(d1) / (F * sd)


def forward_from_parity(strikes, call_px, put_px, T, r, spot):
    """Median of K + e^{rT}(C - P) over the strikes closest to the money."""
    k = np.asarray(strikes, dtype=float)
    c = np.asarray(call_px, dtype=float)
    p = np.asarray(put_px, dtype=float)
    good = (c > 0) & (p > 0)
    if good.sum() < 3:
        return spot * math.exp(r * T)
    k, c, p = k[good], c[good], p[good]
    near = np.argsort(np.abs(k - spot))[:5]
    return float(np.median(k[near] + math.exp(r * T) * (c[near] - p[near])))


def _flip(grid, gex):
    """Zero crossing of total GEX closest to the middle (current spot) of the grid; None if it never crosses."""
    best = None
    mid = grid[len(grid) // 2]
    for i in range(len(grid) - 1):
        g0, g1 = gex[i], gex[i + 1]
        if g0 == 0:
            x = grid[i]
        elif g0 * g1 < 0:
            x = grid[i] + (grid[i + 1] - grid[i]) * (0.0 - g0) / (g1 - g0)
        else:
            continue
        if best is None or abs(x - mid) < abs(best - mid):
            best = x
    return best


def compute_gex(opts, trade_date, spot, rate=RATE, max_expiries=4, band=0.10, grid_pct=0.10, grid_steps=81):
    """opts: DataFrame of one symbol's options on `trade_date` with columns XpryDt, StrkPric, OptnTp, ClsPric,
    SttlmPric, OpnIntrst, TtlTradgVol. Returns a JSON-ready dict, or None if the data cannot support the model."""
    import pandas as pd
    o = opts.copy()
    o["exp"] = pd.to_datetime(o["XpryDt"])
    o["px"] = np.where(o["TtlTradgVol"] > 0, o["ClsPric"], o["SttlmPric"])
    o["px"] = np.where(o["px"] > 0, o["px"], o["SttlmPric"])
    rows = []              # one record per usable OTM option
    expiries = []
    for e in sorted(o["exp"].unique())[:max_expiries]:
        e = pd.Timestamp(e)
        days = (e - pd.Timestamp(trade_date)).days
        if days < 1:
            continue
        T = days / 365.0
        x = o[o["exp"] == e]
        ce = x[x["OptnTp"] == "CE"].set_index("StrkPric")
        pe = x[x["OptnTp"] == "PE"].set_index("StrkPric")
        ks = sorted(set(ce.index) & set(pe.index))
        if len(ks) < 5:
            continue
        F = forward_from_parity(ks, [ce["px"].get(k, 0) for k in ks], [pe["px"].get(k, 0) for k in ks], T, rate, spot)
        for typ, frame in (("CE", ce), ("PE", pe)):
            k = frame.index.to_numpy(dtype=float)
            sel = (k >= F) if typ == "CE" else (k < F)
            sel &= np.abs(k / spot - 1) <= 0.25
            kk = k[sel]
            px = frame["px"].to_numpy(dtype=float)[sel]
            keep = px >= 1.0                                   # ignore sub-rupee prices: tick noise dominates
            if keep.sum() == 0:
                continue
            iv = implied_vol(px[keep], F, kk[keep], T, rate, np.full(keep.sum(), typ == "CE"))
            for kv, s in zip(kk[keep], iv):
                if np.isfinite(s):
                    rows.append((e, T, F, float(kv), float(s)))
        expiries.append((e, T, F))
    if not rows:
        return None
    # open interest for every strike (both sides), then attach IV from the nearest OTM-derived point of the same strike
    import collections
    iv_by = collections.defaultdict(dict)
    for e, T, F, k, s in rows:
        iv_by[e][k] = (s, T, F)
    recs = []
    for e, T, F in expiries:
        if e not in iv_by:
            continue
        ks_iv = np.array(sorted(iv_by[e]))
        sig_iv = np.array([iv_by[e][k][0] for k in ks_iv])
        x = o[o["exp"] == e]
        for typ in ("CE", "PE"):
            f = x[x["OptnTp"] == typ]
            for k, oi in zip(f["StrkPric"].to_numpy(float), f["OpnIntrst"].to_numpy(float)):
                if oi <= 0 or abs(k / spot - 1) > band * 1.5:
                    continue
                sig = float(np.interp(k, ks_iv, sig_iv))     # IV smile interpolated to this strike
                recs.append((e, T, F, k, typ == "CE", oi, sig))
    if not recs:
        return None
    E = np.array([r[0].value for r in recs])
    T = np.array([r[1] for r in recs])
    F = np.array([r[2] for r in recs])
    K = np.array([r[3] for r in recs])
    isc = np.array([r[4] for r in recs])
    OI = np.array([r[5] for r in recs])
    SG = np.array([r[6] for r in recs])
    sign = np.where(isc, 1.0, -1.0)

    def gex_at(S):
        Fg = F * (S / spot)
        g = _gamma_vec(Fg, K, T, SG, rate)
        return sign * g * OI * S * S * 0.01

    def summarise(mask):
        g = gex_at(spot)[mask]
        ks = np.unique(K[mask])
        sel = ks[np.abs(ks / spot - 1) <= band]
        callg = np.array([g[(K[mask] == k) & isc[mask]].sum() for k in sel]) / 1e7
        putg = np.array([g[(K[mask] == k) & ~isc[mask]].sum() for k in sel]) / 1e7
        grid = spot * (1 + np.linspace(-grid_pct, grid_pct, grid_steps))
        prof = np.array([gex_at(s)[mask].sum() for s in grid]) / 1e7
        flip = _flip(grid, prof)
        above, below = sel >= spot, sel < spot
        return {
            "total_cr": round(float(g.sum() / 1e7), 1),
            "flip": None if flip is None else round(float(flip), 0),
            "k": [int(k) for k in sel], "call": [round(float(v), 2) for v in callg], "put": [round(float(v), 2) for v in putg],
            "net": [round(float(a + b), 2) for a, b in zip(callg, putg)],
            "profile": {"s": [round(float(s), 0) for s in grid], "g": [round(float(v), 1) for v in prof]},
            "walls": {"call_gex": int(sel[above][np.argmax(callg[above])]) if above.any() and callg[above].max() > 0 else None,
                      "put_gex": int(sel[below][np.argmin(putg[below])]) if below.any() and putg[below].min() < 0 else None},
        }

    present = set(E.tolist())
    first = next((e.value for e, _, _ in expiries if e.value in present), None)
    atm = {}
    for e, T0, F0 in expiries:
        if e in iv_by:
            ks_iv = np.array(sorted(iv_by[e]))
            atm[pd.Timestamp(e).strftime("%Y-%m-%d")] = round(float(np.interp(F0, ks_iv, [iv_by[e][k][0] for k in ks_iv])) * 100, 2)
    return {"rate": rate, "n_options": int(len(K)), "atm_iv": atm,
            "nearest": {"expiry": pd.Timestamp(first).strftime("%Y-%m-%d"), **summarise(E == first)} if first else None,
            "all": summarise(np.ones(len(K), dtype=bool))}


def _gamma_vec(F, K, T, sigma, r):
    sd = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    return np.exp(-r * T) * npdf(d1) / (F * sd)
