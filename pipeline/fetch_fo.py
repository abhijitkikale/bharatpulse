"""NSE end-of-day derivatives data: F&O bhavcopy (index options/futures) and participant-wise open interest.
Each fetch is cached per day under data/ and never raises, so a blocked source only degrades the Options tab."""
import datetime as dt
import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from config import DATA, HEADERS

FO_DIR = DATA / "fo"
PART_DIR = DATA / "fo_part"
BASE = "https://nsearchives.nseindia.com/content/"
SYMBOLS = ["NIFTY", "BANKNIFTY"]
KEEP = ["TradDt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "ClsPric", "SttlmPric",
        "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol", "UndrlygPric"]
HISTORY_DAYS = 190          # calendar days of history (~130 trading days)


def _get(url):
    """200/404 responses are returned; blocks (403) and network errors are retried with a back-off, then give up."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=40)
            if r.status_code in (200, 404):
                return r
        except requests.RequestException:
            pass
        time.sleep(2 * (attempt + 1))
    return None


def _mark_missing(path, d):
    if (dt.date.today() - d).days > 4:          # an old 404 is a holiday; a recent one may just be unpublished
        path.write_text("", encoding="utf-8")


def reduce_bhavcopy(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only NIFTY / BANKNIFTY index derivatives and the columns the dashboard uses."""
    df = df[df["TckrSymb"].isin(SYMBOLS) & df["FinInstrmTp"].astype(str).str.startswith("ID")]
    return df[KEEP]


def _fo_day(d):
    f = FO_DIR / f"{d:%Y%m%d}.csv"
    if f.exists():
        return
    r = _get(f"{BASE}fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip")
    if r is None:
        return
    if r.status_code == 404:
        _mark_missing(f, d)
        return
    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        reduce_bhavcopy(pd.read_csv(z.open(z.namelist()[0]))).to_csv(f, index=False)
    except Exception:
        pass


def _part_day(d):
    f = PART_DIR / f"{d:%Y%m%d}.csv"
    if f.exists():
        return
    r = _get(f"{BASE}nsccl/fao_participant_oi_{d:%d%m%Y}.csv")
    if r is None:
        return
    if r.status_code == 404:
        _mark_missing(f, d)
        return
    f.write_text(r.text, encoding="utf-8")


def parse_participant(text: str, day) -> list:
    """Participant-wise OI file -> one record per client type with the index-derivative columns we use."""
    df = pd.read_csv(io.StringIO(text), skiprows=1)
    df.columns = [c.strip() for c in df.columns]
    out = []
    for _, r in df.iterrows():
        who = str(r["Client Type"]).strip()
        if who not in ("FII", "DII", "Pro", "Client"):
            continue
        out.append({"date": str(day), "who": who,
                    "fut_long": int(r["Future Index Long"]), "fut_short": int(r["Future Index Short"]),
                    "call_long": int(r["Option Index Call Long"]), "call_short": int(r["Option Index Call Short"]),
                    "put_long": int(r["Option Index Put Long"]), "put_short": int(r["Option Index Put Short"])})
    return out


def fetch_fo(download=True):
    """Download any missing days (unless download=False), then return (bhavcopy frame, participant frame, status)."""
    FO_DIR.mkdir(parents=True, exist_ok=True)
    PART_DIR.mkdir(parents=True, exist_ok=True)
    days = [d.date() for d in pd.bdate_range(dt.date.today() - dt.timedelta(days=HISTORY_DAYS), dt.date.today())]
    if download:
        with ThreadPoolExecutor(3) as ex:          # gentle on NSE: it blocks aggressive clients
            list(ex.map(_fo_day, days))
            list(ex.map(_part_day, days))
    fo = [pd.read_csv(f) for f in sorted(FO_DIR.glob("*.csv")) if f.stat().st_size > 0]
    rows = []
    for f in sorted(PART_DIR.glob("*.csv")):
        if f.stat().st_size == 0:
            continue
        try:
            day = dt.datetime.strptime(f.stem, "%Y%m%d").date()
            rows += parse_participant(f.read_text(encoding="utf-8"), day)
        except Exception:
            continue
    if not fo:
        return None, None, "failed: no F&O files (NSE may be blocking this network)"
    fo = pd.concat(fo, ignore_index=True)
    part = pd.DataFrame(rows)
    last = pd.to_datetime(fo["TradDt"]).max()
    return fo, part, f"ok ({last:%d-%b-%Y}; {fo['TradDt'].nunique()} days, participant OI {part['date'].nunique() if len(part) else 0} days)"
