import csv
import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import stream_journal  # noqa: E402
import stream_trades  # noqa: E402


def _opt(rows, start="2026-11-06 09:26"):
    idx = pd.date_range(start, periods=len(rows), freq="min", tz="Asia/Kolkata")
    return pd.DataFrame(rows, columns=["o", "h", "l", "c", "v"], index=idx)


def _setup(hm, side="CALL", strike=24500):
    return {"time": pd.Timestamp(f"2026-11-06 {hm}", tz="Asia/Kolkata"), "side": side, "strike": strike, "level": "Now VWAP 24474.22", "tag": " (bounce)"}


def test_journal_file_and_csv_hold_each_trade_with_its_option_candles(tmp_path):
    df = _opt([[100, 100, 100, 100, 5], [100, 110, 99, 105, 5], [105, 141, 104, 139, 5], [139, 139, 130, 131, 5]])
    res = stream_trades.run_trades([_setup("09:26"), _setup("09:27", "PUT", 24550)], lambda s: df)
    idx = pd.date_range("2026-11-06 09:15", periods=3, freq="min", tz="Asia/Kolkata")
    index_df = pd.DataFrame({"o": [1.0, 2, 3], "h": [2.0, 3, 4], "l": [0.5, 1, 2], "c": [1.5, 2.5, 3.5], "v": [10, 20, 30]}, index=idx)
    meta = {(24500.0, "CE"): {"symbol": "NSE:NIFTY26N0924500CE", "expiry": "2026-11-09"}}
    doc = stream_journal.build_day(pd.Timestamp("2026-11-06").date(), res, {"close": "2026-11-05", "levels": {"POC": 24552.5}}, index_df, {(24500.0, "CE"): df}, meta,
                                   {"opening_candles": "TradingView (15 candles)"})
    f = stream_journal.write(doc, tmp_path)
    saved = json.loads(f.read_text(encoding="utf-8"))
    t1 = saved["trades"][0]
    assert saved["date"] == "2026-11-06" and saved["levels_used"]["close"] == "2026-11-05" and saved["opening_candles"].startswith("TradingView")
    assert t1["contract"] == "NSE:NIFTY26N0924500CE" and t1["result"] == "TARGET" and t1["pnl_pct"] == 40.0 and t1["exit_time"] == "09:28"
    assert [r[0] for r in t1["option_candles_hm_ohlcv"]] == ["09:26", "09:27", "09:28"]                  # from the entry candle to the exit candle
    assert t1["setups_ignored_while_open"] == ["09:27 PUT 24550 Now VWAP 24474.22 (bounce)"] and len(saved["index_candles_hm_ohlcv"]) == 3 and "saved_at" in saved
    rows = list(csv.DictReader(open(tmp_path / "trades.csv", encoding="utf-8")))
    assert len(rows) == 1 and rows[0]["date"] == "2026-11-06" and rows[0]["result"] == "TARGET" and rows[0]["setups_ignored_while_open"] == "1"
    stream_journal.write({**doc, "date": "2026-11-09"}, tmp_path)                                         # a second day adds rows, nothing is overwritten
    assert len(list(csv.DictReader(open(tmp_path / "trades.csv", encoding="utf-8")))) == 2
