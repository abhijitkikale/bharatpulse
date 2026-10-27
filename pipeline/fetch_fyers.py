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
            d = pd.DataFrame(j["candles"], columns=["t", "o", "h", "l", "c", "v"])
            if d.empty:
                raise RuntimeError("no candles")
            d.index = pd.to_datetime(d["t"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
            out[sym] = d[["o", "h", "l", "c", "v"]].astype(float)
        except Exception as e:
            bad.append(f"{sym} ({str(e)[:60]})")
    if out:
        last = max(v.index.max() for v in out.values())
        return out, f"ok ({last:%d-%b-%Y %H:%M} IST)" + ("; missing " + ", ".join(bad) if bad else "")
    return {}, "failed: " + "; ".join(bad) + " (token may have expired: run tools/fyers_login.py)"
