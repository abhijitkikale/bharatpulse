"""Live option levels for the current expiry only (gamma flip, GEX walls, OI walls, max pain) from one Fyers option-chain call
per index. build_gex() is pure (chains in, small dict out) so it can be tested without the network.

The text block that goes into the second "Live levels" box of the TradingView indicator is made by tv_text(). A level group is
re-sent to Telegram only when a level has moved by at least one strike (see moved())."""
import pandas as pd

import compute_options
from gex import compute_gex

NAME = {"NIFTY": "Nifty", "BANKNIFTY": "Bank Nifty"}
STEP = {"NIFTY": 50, "BANKNIFTY": 100}            # one strike: a level counts as moved when it shifts by at least this much
LEVELS = [("flip", "Gamma flip"), ("gex_call", "GEX call wall"), ("gex_put", "GEX put wall"),
          ("oi_call", "OI call wall"), ("oi_put", "OI put wall"), ("pain", "Max pain")]
BAND = 0.10                                       # same +-10% window around spot as the end-of-day build


def _opts(rows, spot, expiry, today):
    """Chain rows of one expiry -> the frame compute_gex expects. Prices are the live last prices (volume > 0 selects them)."""
    d = pd.DataFrame(rows)
    d = d[d["XpryDt"] == expiry]
    return pd.DataFrame({"XpryDt": d["XpryDt"], "StrkPric": d["StrkPric"], "OptnTp": d["OptnTp"], "ClsPric": d["ltp"], "SttlmPric": d["ltp"],
                         "OpnIntrst": d["oi"], "TtlTradgVol": d["volume"].clip(lower=1.0)})


def _one(sym, c, now):
    today = now.date()
    exp = min(c["rows"], key=lambda r: r["XpryDt"])["XpryDt"]                # the current (nearest) expiry in the rows
    spot = float(c["spot"])
    opts = _opts(c["rows"], spot, exp, today)
    # compute_gex needs at least one day to expiry: on expiry day the model is run as if one day were left (T = 1/365)
    trade_date = today - pd.Timedelta(days=1) if pd.Timestamp(exp).date() <= today else today
    g = compute_gex(opts, str(trade_date), spot, max_expiries=1, surface_expiries=1)
    if g is None or not g.get("nearest"):
        return None
    n = g["nearest"]
    ks = sorted(set(opts.loc[opts["OptnTp"] == "CE", "StrkPric"]) | set(opts.loc[opts["OptnTp"] == "PE", "StrkPric"]))
    ce = opts[opts["OptnTp"] == "CE"].set_index("StrkPric")["OpnIntrst"]
    pe = opts[opts["OptnTp"] == "PE"].set_index("StrkPric")["OpnIntrst"]
    coi = [float(ce.get(k, 0)) for k in ks]
    poi = [float(pe.get(k, 0)) for k in ks]
    if sum(coi) + sum(poi) == 0:
        return None
    near = [i for i, k in enumerate(ks) if abs(k / spot - 1) <= BAND]
    above = [i for i in near if ks[i] >= spot and coi[i] > 0]
    below = [i for i in near if ks[i] <= spot and poi[i] > 0]
    oi_call = ks[max(above, key=lambda i: coi[i])] if above else None
    oi_put = ks[max(below, key=lambda i: poi[i])] if below else None
    lv = {"flip": n["flip"], "gex_call": n["walls"]["call_gex"], "gex_put": n["walls"]["put_gex"], "oi_call": oi_call, "oi_put": oi_put,
          "pain": compute_options.max_pain(ks, coi, poi)}
    return {"spot": round(spot, 2), "expiry": exp, "net_cr": n["total_cr"], "pcr": compute_options._pcr(sum(poi), sum(coi)),
            "levels": {k: (None if v is None else round(float(v), 0)) for k, v in lv.items()}}


def build_gex(chains, now):
    """chains: output of fetch_fyers.fetch_chains(current_only=True); now: tz-aware Timestamp (IST). Returns {sym: block} (symbols
    that cannot be computed are left out) or None if nothing could be computed."""
    out = {}
    for sym, c in chains.items():
        try:
            b = _one(sym, c, now)
        except Exception:                                # a bad chain for one index must not stop the other
            b = None
        if b:
            out[sym] = b
    return out or None


def tv_text(sym, block, now):
    """The text for the 'Live levels' box of the indicator (one index)."""
    head = f"# BharatPulse {NAME.get(sym, sym)} LIVE {now.strftime('%Y-%m-%d %H:%M')} IST | expiry {block['expiry']} | spot {block['spot']:g}"
    rows = [f"{label}={block['levels'][k]:g}" for k, label in LEVELS if block["levels"].get(k) is not None]
    return "\n".join([head, "@time=" + now.strftime("%H:%M")] + rows)


def moved(sym, new, old):
    """True when there is no earlier message, or any level appeared / vanished / shifted by at least one strike."""
    if old is None:
        return True
    step = STEP.get(sym, 50)
    for k, _ in LEVELS:
        a, b = new["levels"].get(k), old["levels"].get(k)
        if (a is None) != (b is None) or (a is not None and abs(a - b) >= step):
            return True
    return False
