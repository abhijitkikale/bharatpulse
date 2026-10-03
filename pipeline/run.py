"""Daily entry point: fetch -> compute -> build. `python run.py --cached` rebuilds from cached data only."""
import sys, json, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from config import *
import fetch, compute, build

DATA.mkdir(exist_ok=True)


def main(cached=False):
    status = {}
    allx, status["NSE index history"] = fetch.fetch_index_history()
    cons, status["NSE constituents"] = fetch.fetch_constituents()
    syms = sorted({s + ".NS" for fn in UNIVERSE_FILES if fn in cons for s in cons[fn]["Symbol"] if not s.startswith("DUMMY")})
    if cached:
        px = pd.read_pickle(DATA / "stock_prices.pkl"); status["Stock prices"] = "cached"
        mc = json.loads((DATA / "mcap.json").read_text()); status["Market caps"] = "cached"
    else:
        px, status["Stock prices"] = fetch.fetch_stock_prices(syms)
        mc, status["Market caps"] = fetch.fetch_float_mcap(syms, px)
    gcache = DATA / "global.pkl"
    gpx, status["Global indices + FX"] = (pd.read_pickle(gcache), "cached") if cached and gcache.exists() else fetch.fetch_global()
    if gpx is not None:
        gpx.to_pickle(gcache)
    elif gcache.exists():
        gpx = pd.read_pickle(gcache); status["Global indices + FX"] += "; using cached"
    fii, status["FII/DII"] = fetch.fetch_fiidii()
    payload = compute.assemble(allx, cons, px, mc, gpx, fii, status)
    (DATA / "dashboard.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    out = build.render(payload)
    print("Built", out, f"({out.stat().st_size/1e6:.1f} MB)")
    for k, v in status.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(cached="--cached" in sys.argv)
