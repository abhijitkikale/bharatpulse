"""Source fetchers. Each returns (data, status) and never raises, so one failed source can't break the build."""
import io, time, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import pandas as pd, requests, yfinance as yf
from config import *

IDX_CACHE.mkdir(parents=True, exist_ok=True)


def _get(url, tries=3, **kw):
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=25, **kw)
            return r
        except requests.RequestException:
            time.sleep(1.5 * (i + 1))
    return None


# ---------- NSE all-indices daily close archive (closes + P/E, P/B, yield) ----------
def _fetch_day(d: dt.date):
    f = IDX_CACHE / f"{d:%Y%m%d}.csv"
    if f.exists():
        return f.stat().st_size > 0
    r = _get(f"{ARCHIVE}ind_close_all_{d:%d%m%Y}.csv")
    if r is None:
        return False
    if r.status_code == 200 and r.text.startswith("Index Name"):
        f.write_text(r.text, encoding="utf-8")
        return True
    if r.status_code == 404 and (dt.date.today() - d).days > 4:
        f.write_text("", encoding="utf-8")        # holiday marker; recent 404s may just be unpublished
    return False


def fetch_index_history():
    days = [d.date() for d in pd.bdate_range(HISTORY_START, dt.date.today())]
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(_fetch_day, days))
    frames = []
    for f in sorted(IDX_CACHE.glob("*.csv")):
        if f.stat().st_size == 0:
            continue
        df = pd.read_csv(f)
        df["date"] = pd.to_datetime(df["Index Date"], format="%d-%m-%Y", errors="coerce")
        frames.append(df)
    if not frames:
        return None, "failed: no index files"
    allx = pd.concat(frames)
    allx["Index Name"] = allx["Index Name"].str.strip()
    for c in ["Closing Index Value", "P/E", "P/B", "Div Yield"]:
        allx[c] = pd.to_numeric(allx[c], errors="coerce")
    last = allx["date"].max()
    return allx, f"ok ({last:%d-%b-%Y})"


# ---------- constituents ----------
def fetch_constituents():
    out, status = {}, []
    files = {v[1] for d in (BENCHMARKS, SECTORS) for v in d.values() if v[1]} | set(UNIVERSE_FILES)
    def one(fn):
        r = _get(ARCHIVE + fn)
        if r is not None and r.status_code == 200:
            return fn, pd.read_csv(io.StringIO(r.text))
        return fn, None
    with ThreadPoolExecutor(8) as ex:
        for fn, df in ex.map(one, files):
            if df is None:
                status.append(fn)
            else:
                out[fn] = df
    return out, ("ok" if not status else "partial; missing " + ", ".join(status))


# ---------- stocks via Yahoo ----------
def _yf_close(symbols, period=None, start=None):
    frames = []
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        for attempt in range(3):
            try:
                d = yf.download(chunk, period=period, start=start, auto_adjust=True, progress=False, threads=True)
                c = d["Close"] if len(chunk) > 1 else d["Close"].to_frame(chunk[0])
                frames.append(c)
                break
            except Exception:
                time.sleep(3 * (attempt + 1))
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def fetch_stock_prices(symbols):
    cache = DATA / "stock_prices.pkl"
    old = pd.read_pickle(cache) if cache.exists() else None
    if old is not None and set(symbols) <= set(old.columns):
        new = _yf_close(symbols, start=(old.index.max() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"))
        px = pd.concat([old[old.index < new.index.min()], new]) if not new.empty else old
    else:
        px = _yf_close(symbols, period="5y")
    px = px.dropna(how="all").sort_index()
    px = px[~px.index.duplicated(keep="last")]
    if px.empty:
        return old, "failed"
    # Yahoo sometimes returns a stale batch: refetch tickers whose last price lags the rest, in small batches.
    for attempt in range(2):
        last = px.apply(lambda c: c.last_valid_index())
        stale = [s for s in px.columns if last[s] is None or last[s] < px.index.max() - pd.Timedelta(days=4)]
        if not stale:
            break
        for i in range(0, len(stale), 20):
            fix = _yf_close(stale[i:i + 20], period="5y")
            if not fix.empty:
                px = px.reindex(px.index.union(fix.index))
                for c in fix.columns:
                    px[c] = fix[c].reindex(px.index)
        time.sleep(2)
    px.to_pickle(cache)
    return px, f"ok ({px.index.max():%d-%b-%Y}, {px.shape[1]} tickers)"


def fetch_float_mcap(symbols, prices):
    """Free-float market cap = Yahoo floatShares x last price; falls back to total marketCap (fast_info)."""
    cache = DATA / "mcap.json"
    import json
    prev = json.loads(cache.read_text()) if cache.exists() else {}
    def one(s):
        last = prices[s].dropna().iloc[-1] if s in prices and prices[s].notna().any() else None
        for attempt in range(3):
            try:
                info = yf.Ticker(s).get_info()
                fs, mc = info.get("floatShares"), info.get("marketCap")
                if fs and last:
                    return s, fs * last, "float"
                if mc:
                    return s, mc, "total"
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        try:
            mc = yf.Ticker(s).fast_info["market_cap"]
            if mc:
                return s, mc, "total"
        except Exception:
            pass
        return s, None, None
    today = dt.date.today()
    fresh = lambda e: e.get("v") and (today - dt.date.fromisoformat(e.get("t", "2000-01-01"))).days < 35
    todo = [s for s in symbols if not fresh(prev.get(s, {}))]
    done = 0
    with ThreadPoolExecutor(4) as ex:
        for s, v, kind in ex.map(one, todo):
            if v:
                prev[s] = {"v": v, "kind": kind, "t": today.isoformat()}
            done += 1
            if done % 100 == 0:
                cache.write_text(json.dumps(prev))
    cache.write_text(json.dumps(prev))
    got = sum(1 for s in symbols if prev.get(s, {}).get("v"))
    return {s: prev[s] for s in symbols if s in prev}, f"ok ({got}/{len(symbols)})"


# ---------- global indices + FX ----------
def fetch_global():
    syms = [v[0] for v in GLOBAL.values()] + sorted({v[1][0] for v in GLOBAL.values() if v[1]} | {INR_FX[0]})
    px = _yf_close(syms, period="6y")
    if px.empty:
        return None, "failed"
    return px.ffill(), f"ok ({px.index.max():%d-%b-%Y})"


# ---------- FII / DII (NSE provisional; appended daily) ----------
def fetch_fiidii():
    f = DATA / "fiidii.csv"
    hist = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["date", "FII", "DII"])
    try:
        s = requests.Session(); s.headers.update(HEADERS)
        s.get("https://www.nseindia.com", timeout=20)
        r = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=20).json()
        row = {}
        for x in r:
            d = pd.to_datetime(x["date"], format="%d-%b-%Y")
            row["date"] = d.strftime("%Y-%m-%d")
            row["FII" if x["category"].startswith("FII") else "DII"] = float(x["netValue"])
        hist = pd.concat([hist[hist["date"] != row["date"]], pd.DataFrame([row])]).sort_values("date")
        hist.to_csv(f, index=False)
        status = f"ok ({row['date']}; {len(hist)} days stored)"
    except Exception as e:
        status = f"stale: using stored data ({type(e).__name__})"
    return hist, status


# ---------- forward EPS (Yahoo analyst consensus, next fiscal year); refreshed weekly ----------
def fetch_forward_eps(symbols):
    import json
    cache = DATA / "feps.json"
    prev = json.loads(cache.read_text()) if cache.exists() else {}
    today = dt.date.today()
    fresh = lambda e: "e" in e and (today - dt.date.fromisoformat(e.get("t", "2000-01-01"))).days < 7
    def one(s):
        for attempt in range(3):
            try:
                eps = yf.Ticker(s).get_info().get("forwardEps")
                return s, (float(eps) if eps is not None else 0.0)
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return s, None
    todo = [s for s in symbols if not fresh(prev.get(s, {}))]
    done = 0
    with ThreadPoolExecutor(4) as ex:
        for s, eps in ex.map(one, todo):
            if eps is not None:
                prev[s] = {"e": eps, "t": today.isoformat()}
            done += 1
            if done % 100 == 0:
                cache.write_text(json.dumps(prev))
    cache.write_text(json.dumps(prev))
    got = sum(1 for s in symbols if prev.get(s, {}).get("e", 0) > 0)
    return {s: prev[s] for s in symbols if s in prev}, f"ok ({got}/{len(symbols)} with estimates)"


def record_forward_pe(today_values):
    """Append today's forward P/E per index to data/fwd_pe.csv (committed by CI, so history accumulates)."""
    f = DATA / "fwd_pe.csv"
    hist = pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["date", "index", "fpe"])
    day = dt.date.today().isoformat()
    rows = pd.DataFrame([{"date": day, "index": k, "fpe": round(v, 2)} for k, v in today_values.items()])
    if not rows.empty:
        hist = pd.concat([hist[hist["date"] != day], rows]).sort_values(["index", "date"])
        hist.to_csv(f, index=False)
    return hist
