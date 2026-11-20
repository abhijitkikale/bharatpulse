"""Instrument validation against the FYERS instrument master (the public NSE F&O symbol master, https://public.fyers.in/sym_details/NSE_FO.csv, one row per contract).

Columns used (FYERS symbol-master layout): 0 fytoken, 1 description, 2 instrument type, 3 minimum lot size, 4 tick size, 8 expiry (epoch seconds), 9 symbol ticker
(e.g. NSE:NIFTY26NOV25300CE). The ticker column is found by value, not by position, so a changed layout makes validation FAIL (and LIVE refuse to order) instead of
validating the wrong thing. The file is downloaded once a day into execution/data/ (git-ignored)."""
import dataclasses
import pathlib
import time

from . import config

URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
FILE = config.DATA / "NSE_FO.csv"
MAX_AGE_S = 20 * 3600


@dataclasses.dataclass
class Check:
    ok: bool
    lot: int = 0
    tick: float = 0.0
    reason: str = ""


class Instruments:
    def __init__(self, path=None, rows=None, fetch=True):
        """rows: {ticker: {"lot": int, "tick": float, "expiry": epoch}} for tests; otherwise the CSV file is used (downloaded when missing or old and fetch=True)."""
        self.path, self.rows, self.fetch, self._cache = pathlib.Path(path or FILE), rows, fetch, {}

    def _ensure_file(self):
        try:
            if self.path.exists() and time.time() - self.path.stat().st_mtime < MAX_AGE_S:
                return True
            if not self.fetch:
                return self.path.exists()
            import requests
            r = requests.get(URL, timeout=60)
            r.raise_for_status()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(r.content)
            return True
        except Exception:
            return self.path.exists()

    def lookup(self, symbol):
        if self.rows is not None:
            return self.rows.get(symbol)
        if symbol in self._cache:
            return self._cache[symbol]
        if not self._ensure_file():
            return None
        found = None
        needle = f",{symbol},"
        try:
            with open(self.path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if needle in line or line.rstrip("\n").endswith("," + symbol):
                        parts = line.rstrip("\n").split(",")
                        if symbol in parts:
                            found = {"lot": int(float(parts[3])), "tick": float(parts[4]), "expiry": int(float(parts[8])) if parts[8] else 0}
                            break
        except (OSError, ValueError, IndexError):
            found = None
        self._cache[symbol] = found
        return found

    def validate(self, symbol, qty, option_side, now_epoch=None):
        """Check the contract exists, is the right type (CE for a CALL, PE for a PUT), is not expired, and that qty is a whole number of lots."""
        row = self.lookup(symbol)
        if row is None:
            return Check(False, reason=f"{symbol} was not found in the FYERS instrument master (or the master could not be read)")
        want = "CE" if option_side == "CALL" else "PE"
        if not symbol.endswith(want):
            return Check(False, reason=f"{symbol} is not a {want} contract but the dashboard side is {option_side}")
        if row.get("expiry") and row["expiry"] < (now_epoch or time.time()) - 24 * 3600:
            return Check(False, reason=f"{symbol} has expired")
        lot = int(row["lot"])
        if lot <= 0 or qty % lot != 0:
            return Check(False, lot=lot, reason=f"quantity {qty} is not a whole number of lots (lot size {lot})")
        return Check(True, lot=lot, tick=float(row.get("tick") or config.DEFAULTS["tick"]))

    def lot_size(self, symbol):
        row = self.lookup(symbol)
        return int(row["lot"]) if row else None
