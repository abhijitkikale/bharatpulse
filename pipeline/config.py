"""Static configuration: which indices, sectors and global benchmarks the dashboard tracks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
IDX_CACHE = DATA / "idx"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
ARCHIVE = "https://archives.nseindia.com/content/indices/"

HISTORY_START = "2020-09-01"   # NSE index history for returns (5Y annualised) and P/E chart

# label -> (name in NSE all-indices file, constituent csv or None)
BENCHMARKS = {
    "Nifty 50": ("Nifty 50", "ind_nifty50list.csv"),
    "Nifty Next 50": ("Nifty Next 50", "ind_niftynext50list.csv"),
    "Nifty Midcap 150": ("Nifty Midcap 150", "ind_niftymidcap150list.csv"),
    "Nifty Smallcap 250": ("Nifty Smallcap 250", "ind_niftysmallcap250list.csv"),
    "Nifty Microcap 250": ("Nifty Microcap 250", "ind_niftymicrocap250_list.csv"),
    "Nifty 500": ("Nifty 500", "ind_nifty500list.csv"),
}

SECTORS = {
    "Pharma": ("Nifty Pharma", "ind_niftypharmalist.csv"),
    "Media": ("Nifty Media", "ind_niftymedialist.csv"),
    "Defence": ("Nifty India Defence", "ind_niftyindiadefence_list.csv"),
    "Private Bank": ("Nifty Private Bank", None),          # derived: Bank minus PSU Bank
    "Metal": ("Nifty Metal", "ind_niftymetallist.csv"),
    "Bank": ("Nifty Bank", "ind_niftybanklist.csv"),
    "Realty": ("Nifty Realty", "ind_niftyrealtylist.csv"),
    "Tourism": ("Nifty India Tourism", "ind_niftyindiatourism_list.csv"),
    "Energy": ("Nifty Energy", "ind_niftyenergylist.csv"),
    "Services Sector": ("Nifty Services Sector", "ind_niftyservicelist.csv"),
    "Capital Market": ("Nifty Capital Markets", None),     # derived: curated list below
    "Financial Services": ("Nifty Financial Services", "ind_niftyfinancelist.csv"),
    "IT": ("Nifty IT", "ind_niftyitlist.csv"),
    "Commodities": ("Nifty Commodities", "ind_niftycommoditieslist.csv"),
    "PSE": ("Nifty PSE", "ind_niftypselist.csv"),
    "Oil & Gas": ("Nifty Oil & Gas", "ind_niftyoilgaslist.csv"),
    "PSU Bank": ("Nifty PSU Bank", "ind_niftypsubanklist.csv"),
    "MNC": ("Nifty MNC", "ind_niftymnclist.csv"),
    "CPSE": ("Nifty CPSE", "ind_niftycpselist.csv"),
    "Auto": ("Nifty Auto", "ind_niftyautolist.csv"),
    "Infrastructure": ("Nifty Infrastructure", "ind_niftyinfralist.csv"),
    "India Consumption": ("Nifty India Consumption", "ind_niftyconsumptionlist.csv"),
    "FMCG": ("Nifty FMCG", "ind_niftyfmcglist.csv"),
    "Consumer Durables": ("Nifty Consumer Durables", "ind_niftyconsumerdurableslist.csv"),
    "Manufacturing": ("Nifty India Manufacturing", "ind_niftyindiamanufacturing_list.csv"),
}

# Approximate Nifty Capital Markets membership (no public constituent file); filtered to the tracked universe.
CAPITAL_MARKET_SYMBOLS = ["BSE", "CDSL", "ANGELONE", "MCX", "CAMS", "KFINTECH", "360ONE", "NUVAMA", "IEX",
                          "ANANDRATHI", "MOTILALOFS", "NAM-INDIA", "HDFCAMC", "NIPPONAMC", "UTIAMC", "ABSLAMC",
                          "JMFINANCIL", "ISEC", "5PAISA", "GROWW"]

UNIVERSE_FILES = ["ind_nifty500list.csv", "ind_niftymicrocap250_list.csv"]   # ~750 stocks

# label -> (yahoo symbol, fx symbol to convert local->USD, fx quoted as 'local per USD'?)
GLOBAL = {
    "S&P 500 (USA)": ("^GSPC", None),
    "Dow Jones (USA)": ("^DJI", None),
    "NASDAQ (USA)": ("^IXIC", None),
    "Russell 2000 (USA)": ("^RUT", None),
    "S&P/TSX Composite (Canada)": ("^GSPTSE", ("CAD=X", "per_usd")),
    "Bovespa (Brazil)": ("^BVSP", ("BRL=X", "per_usd")),
    "FTSE 100 (UK)": ("^FTSE", ("GBPUSD=X", "usd_per")),
    "DAX (Germany)": ("^GDAXI", ("EURUSD=X", "usd_per")),
    "EURO STOXX 50 (Europe)": ("^STOXX50E", ("EURUSD=X", "usd_per")),
    "CAC 40 (France)": ("^FCHI", ("EURUSD=X", "usd_per")),
    "Nikkei 225 (Japan)": ("^N225", ("JPY=X", "per_usd")),
    "Hang Seng (China)": ("^HSI", ("HKD=X", "per_usd")),
    "ASX 200 (Australia)": ("^AXJO", ("AUDUSD=X", "usd_per")),
    "KOSPI (S.Korea)": ("^KS11", ("KRW=X", "per_usd")),
}
INR_FX = ("INR=X", "per_usd")

PERIODS = ["1W", "1M", "3M", "6M", "1Y", "3Y", "5Y"]
