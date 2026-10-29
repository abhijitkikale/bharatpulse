"""Fyers market data (a NON-TRADING, data-only API app). Used when a valid access token is available; every function
reports problems instead of raising so the build can fall back to the NSE / Yahoo sources.

The token comes from the environment (FYERS_APP_ID, FYERS_TOKEN - set in GitHub secrets by tools/fyers_login.py) or, on a
local PC, from data/fyers_token.json. It is never printed or written into any output; error text only says what failed."""
import datetime as dt
import json
import os
import pathlib

import pandas as pd
import requests

DATA = pathlib.Path(__file__).resolve().parent.parent / "data"
BASE = "https://api-t1.fyers.in/data"
INDEX = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX"}


def _auth():
    app, tok = os.environ.get("FYERS_APP_ID", "").strip(), os.environ.get("FYERS_TOKEN", "").strip()
    if not (app and tok):
        f = DATA / "fyers_token.json"
        if f.exists():
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                app, tok = j.get("app_id", ""), j.get("access_token", "")
            except Exception:
                pass
    return {"Authorization": f"{app}:{tok}"} if app and tok else None


def _get(path, headers, **params):
    r = requests.get(f"{BASE}/{path}", headers=headers, params=params, timeout=40)
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError(f"HTTP {r.status_code}")
    if j.get("s") != "ok":
        raise RuntimeError(str(j.get("message") or f"HTTP {r.status_code}")[:80])
    return j


def candles_frame(candles):
    """Fyers candles [[epoch, o, h, l, c, v], ...] -> DataFrame(o, h, l, c, v) on an IST index. Fyers can repeat candles inside one
    response (seen for the current session), which would double the volume, so each timestamp is kept once."""
    d = pd.DataFrame(candles, columns=["t", "o", "h", "l", "c", "v"])
    if d.empty:
        raise RuntimeError("no candles")
    d = d.drop_duplicates("t", keep="last").sort_values("t")
    d.index = pd.to_datetime(d["t"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    return d[["o", "h", "l", "c", "v"]].astype(float)


def fetch_intraday(days=10):
    """{sym: DataFrame(o, h, l, c, v)} of 1-minute index candles (IST index). The index's own traded volume is included,
    so no stock-by-stock sum is needed. Returns (frames, status text); frames is {} if Fyers cannot be used."""
    h = _auth()
    if h is None:
        return {}, "not configured (run tools/fyers_login.py)"
    out, bad = {}, []
    today = dt.date.today()
    for sym, fy in INDEX.items():
        try:
            j = _get("history", h, symbol=fy, resolution="1", date_format="1", cont_flag="1",
                     range_from=(today - dt.timedelta(days=days)).isoformat(), range_to=today.isoformat())
            out[sym] = candles_frame(j["candles"])
        except Exception as e:
            bad.append(f"{sym} ({str(e)[:60]})")
    if out:
        last = max(v.index.max() for v in out.values())
        return out, f"ok ({last:%d-%b-%Y %H:%M} IST)" + ("; missing " + ", ".join(bad) if bad else "")
    return {}, "failed: " + "; ".join(bad) + " (token may have expired: run tools/fyers_login.py)"


# ---------------- option chain: a provisional "today" while NSE's F&O file is not out yet ----------------
FO_COLS = ["TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "ClsPric", "SttlmPric",
           "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "UndrlygPric"]


def fetch_chains(strikes=100):
    """{sym: {"spot": index last price, "rows": [dict per option]}} for EVERY listed expiry (about 100 strikes around the money
    each). Returns ({}, text) when Fyers cannot be used."""
    h = _auth()
    if h is None:
        return {}, "not configured"
    out, bad = {}, []
    for sym, fy in INDEX.items():
        try:
            first = _get("options-chain-v3", h, symbol=fy, strikecount=strikes)["data"]
            spot = next(float(r["ltp"]) for r in first["optionsChain"] if not r.get("option_type"))
            rows = []
            for e in first["expiryData"]:
                d = first if e is first["expiryData"][0] else _get("options-chain-v3", h, symbol=fy, strikecount=strikes, timestamp=e["expiry"])["data"]
                xp = dt.datetime.strptime(e["date"], "%d-%m-%Y").date().isoformat()
                for r in d["optionsChain"]:
                    if r.get("option_type") in ("CE", "PE") and r.get("oi") is not None:
                        rows.append({"XpryDt": xp, "StrkPric": float(r["strike_price"]), "OptnTp": r["option_type"], "ltp": float(r["ltp"]),
                                     "oi": float(r["oi"]), "prev_oi": float(r.get("prev_oi") or 0), "volume": float(r.get("volume") or 0)})
            if not rows:
                raise RuntimeError("empty chain")
            out[sym] = {"spot": spot, "rows": rows}
        except Exception as e:
            bad.append(f"{sym} ({str(e)[:60]})")
    return out, ("ok" if out else "failed: " + "; ".join(bad)) + (("; missing " + ", ".join(bad)) if out and bad else "")


def provisional_fo(fo, trade_date, chains, tol=0.01):
    """Add one provisional trading day to the NSE F&O frame from Fyers chains. Strikes Fyers returns use Fyers' open interest and
    prices; every other live contract keeps the previous day's NSE open interest (far out-of-the-money open interest hardly moves
    in a day) with no price, so put-call totals stay comparable. Trust check: on the strikes both sources list, Fyers'
    previous-day open interest must equal NSE's last file within `tol` and cover most of the book; otherwise that symbol is skipped.
    A contract expiring on trade_date is carried like any other (NSE's own file for an expiry day still lists it). Rows are
    flagged Prov=True. Returns (frame, [symbols added])."""
    day = pd.Timestamp(trade_date)
    f = fo.copy()
    f["_d"] = pd.to_datetime(f["TradDt"])
    prior = f[(f["_d"] < day) & f["OptnTp"].isin(["CE", "PE"])]
    if prior.empty:
        return fo, []
    prev_day = prior["_d"].max()
    added, new = [], []
    for sym, c in chains.items():
        p = prior[(prior["_d"] == prev_day) & (prior["TckrSymb"] == sym) & (pd.to_datetime(prior["XpryDt"]) >= day)]
        if p.empty or not c["rows"]:
            continue
        fy = pd.DataFrame(c["rows"])
        fy = fy[pd.to_datetime(fy["XpryDt"]) > day]
        keys = set(zip(fy["XpryDt"], fy["StrkPric"], fy["OptnTp"]))
        both = p.assign(StrkPric=p["StrkPric"].astype(float), XpryDt=p["XpryDt"].astype(str)).set_index(["XpryDt", "StrkPric", "OptnTp"])["OpnIntrst"]
        j = fy.set_index(["XpryDt", "StrkPric", "OptnTp"])[["prev_oi"]].join(both.rename("nse_prev"), how="inner")
        if j.empty or j["nse_prev"].sum() < 0.6 * p.loc[pd.to_datetime(p["XpryDt"]) > day, "OpnIntrst"].sum() or abs(j["prev_oi"].sum() / j["nse_prev"].sum() - 1) > tol:
            continue
        carry = p[[(x, k, t) not in keys for x, k, t in zip(p["XpryDt"].astype(str), p["StrkPric"].astype(float), p["OptnTp"])]]
        a = pd.DataFrame({"TradDt": day.strftime("%Y-%m-%d"), "FinInstrmTp": "IDO", "TckrSymb": sym, "XpryDt": fy["XpryDt"], "StrkPric": fy["StrkPric"],
                          "OptnTp": fy["OptnTp"], "ClsPric": fy["ltp"], "SttlmPric": fy["ltp"], "OpnIntrst": fy["oi"],
                          "ChngInOpnIntrst": fy["oi"] - fy["prev_oi"], "TtlTradgVol": fy["volume"], "UndrlygPric": c["spot"]})
        b = carry[FO_COLS].copy()
        b["TradDt"], b["ClsPric"], b["SttlmPric"], b["ChngInOpnIntrst"], b["TtlTradgVol"], b["UndrlygPric"] = day.strftime("%Y-%m-%d"), 0.0, 0.0, 0.0, 0.0, c["spot"]
        a, b = a[FO_COLS].assign(Prov=True), b.assign(Prov=True)
        new += [a, b]
        added.append(sym)
    if not new:
        return fo, []
    return pd.concat([fo] + new, ignore_index=True), added
