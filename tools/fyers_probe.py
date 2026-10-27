"""Read-only check of what the Fyers data API returns for our indices (run after tools/fyers_login.py).
Prints status codes and the SHAPE of each response and saves the raw JSON under data/_fyers_probe/ (git-ignored).
Never prints the token."""
import datetime as dt
import json
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
tok = json.loads((ROOT / "data" / "fyers_token.json").read_text(encoding="utf-8"))
H = {"Authorization": f"{tok['app_id']}:{tok['access_token']}"}
D = "https://api-t1.fyers.in/data"
OUT = ROOT / "data" / "_fyers_probe"
OUT.mkdir(exist_ok=True)


def shape(x, depth=0):
    if isinstance(x, dict):
        return {k: shape(v, depth + 1) if depth < 3 else "..." for k, v in list(x.items())[:25]}
    if isinstance(x, list):
        return [f"list[{len(x)}]", shape(x[0], depth + 1) if x else None]
    return type(x).__name__ if not isinstance(x, (int, float)) else x


def call(name, url, **params):
    try:
        r = requests.get(url, headers=H, params=params, timeout=40)
        j = r.json()
    except Exception as e:
        print(f"{name}: FAILED {type(e).__name__}")
        return None
    (OUT / f"{name}.json").write_text(json.dumps(j), encoding="utf-8")
    print(f"\n== {name}: HTTP {r.status_code}, s={j.get('s')}, message={j.get('message')}")
    print(json.dumps(shape(j), indent=1, default=str)[:1800])
    return j


call("quotes", f"{D}/quotes", symbols="NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,NSE:RELIANCE-EQ")
today = dt.date.today()
for sym, tag in (("NSE:NIFTY50-INDEX", "nifty"), ("NSE:NIFTYBANK-INDEX", "banknifty"), ("NSE:RELIANCE-EQ", "reliance")):
    call(f"history_1m_{tag}", f"{D}/history", symbol=sym, resolution="1", date_format="1",
         range_from=(today - dt.timedelta(days=7)).isoformat(), range_to=today.isoformat(), cont_flag="1")
call("optionchain_nifty", f"{D}/options-chain-v3", symbol="NSE:NIFTY50-INDEX", strikecount="5")
call("optionchain_banknifty", f"{D}/options-chain-v3", symbol="NSE:NIFTYBANK-INDEX", strikecount="5")
print("\nRaw responses saved in data/_fyers_probe/")
