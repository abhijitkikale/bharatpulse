"""NSE end-of-day price/volume file for every listed security, cached per day under data/bhav/.
Feeds the volume leaders, volume spikes and whole-market breadth. Never raises: a missing day just means fewer points."""
import io
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import fetch
from config import DATA

BHAV_DIR = DATA / "bhav"
KEEP = ["SYMBOL", "SERIES", "PREV_CLOSE", "CLOSE_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS", "DELIV_PER"]
KEEP_DAYS = 65                      # about three months of trading days


def _day(d):
    f = BHAV_DIR / f"{d:%Y%m%d}.csv"
    if f.exists():
        return
    r = fetch._get(fetch.BHAV_URL.format(d))
    if r is None or r.status_code != 200:
        return
    try:
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = [c.strip() for c in df.columns]
        df["SERIES"] = df["SERIES"].astype(str).str.strip()
        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
        df[KEEP].to_csv(f, index=False)
    except Exception:
        pass


def fetch_bhav(trading_dates, keep_days=KEEP_DAYS, download=True):
    """Return (frame of all cached days with a `date` column, status). `trading_dates` come from NSE's index history."""
    BHAV_DIR.mkdir(parents=True, exist_ok=True)
    days = sorted(pd.Timestamp(x) for x in trading_dates)[-keep_days:]
    if download and days:
        with ThreadPoolExecutor(3) as ex:                     # gentle on NSE
            list(ex.map(_day, days))
    frames = []
    for d in days:
        f = BHAV_DIR / f"{d:%Y%m%d}.csv"
        if f.exists() and f.stat().st_size > 0:
            x = pd.read_csv(f)
            x["date"] = d
            frames.append(x)
    if not frames:
        return None, "failed: no NSE price files"
    allb = pd.concat(frames, ignore_index=True)
    return allb, f"ok ({allb['date'].max():%d-%b-%Y}; {allb['date'].nunique()} days)"
