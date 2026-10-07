"""Daily entry point: fetch -> compute -> build. `python run.py --cached` rebuilds from cached data only."""
import sys, json, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
from config import *
import fetch, compute, build, fetch_fo, compute_options

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
        idx_dates = sorted(allx["date"].dt.normalize().unique()) if allx is not None else []
        px, notes = fetch.patch_prices_with_nse(px, idx_dates)          # Yahoo publishes NSE closes late; NSE's own file does not
        if notes:
            status["Stock prices"] += "; " + ", ".join(notes)
        elif idx_dates and px is not None and px.dropna(how="all").index.max() < pd.Timestamp(idx_dates[-1]):
            status["Stock prices"] = "stale: prices end " + f"{px.dropna(how='all').index.max():%d-%b-%Y}" + " but NSE index data is newer"
        mc, status["Market caps"] = fetch.fetch_float_mcap(syms, px)
    gcache = DATA / "global.pkl"
    gpx, status["Global indices + FX"] = (pd.read_pickle(gcache), "cached") if cached and gcache.exists() else fetch.fetch_global()
    if gpx is not None:
        gpx.to_pickle(gcache)
    elif gcache.exists():
        gpx = pd.read_pickle(gcache); status["Global indices + FX"] += "; using cached"
    fii, status["FII/DII"] = fetch.fetch_fiidii()
    if cached:
        feps = json.loads((DATA / "feps.json").read_text()) if (DATA / "feps.json").exists() else {}; status["Forward EPS"] = "cached"
    else:
        feps, status["Forward EPS"] = fetch.fetch_forward_eps(syms)
    if cached:
        mf = DATA / "move.csv"; hf = DATA / "hy_oas.csv"
        credit = (pd.read_csv(mf, parse_dates=["date"]).set_index("date")["move"], pd.read_csv(hf, parse_dates=["date"]).set_index("date")["hy"]) if mf.exists() and hf.exists() else None
        status["MOVE + HY spread"] = "cached"
    else:
        move, hy, status["MOVE + HY spread"] = fetch.fetch_credit()
        credit = (move, hy) if move is not None else None
    fo, part, status["Options (F&O bhavcopy, participant OI)"] = fetch_fo.fetch_fo(download=not cached)
    vix = allx[allx["Index Name"] == "India VIX"].set_index("date")["Closing Index Value"].sort_index() if allx is not None else None
    options = compute_options.build_options(fo, part, vix)
    payload = compute.assemble(allx, cons, px, mc, gpx, fii, status, feps, credit=credit, options=options)
    today_vals = {ix["label"]: ix["fpe"]["now"] for ix in payload["indices"] if "fpe" in ix}
    fwd_file = DATA / "fwd_pe.csv"
    if not cached and today_vals:
        hist = fetch.record_forward_pe(today_vals, px.index.max().date())
    else:
        hist = pd.read_csv(fwd_file) if fwd_file.exists() else pd.DataFrame(columns=["date", "index", "fpe"])
    for ix in payload["indices"]:
        if "fpe" in ix:
            g = hist[hist["index"] == ix["label"]]
            ix["fpe"]["rec"] = {"d": list(g["date"]), "v": [round(float(v), 2) for v in g["fpe"]]}
    (DATA / "dashboard.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    out = build.render(payload)
    print("Built", out, f"({out.stat().st_size/1e6:.1f} MB)")
    for k, v in status.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main(cached="--cached" in sys.argv)
