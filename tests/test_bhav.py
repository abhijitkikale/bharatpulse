import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd

from fetch import apply_bhav_returns


def _px(with_placeholder=False):
    idx = pd.to_datetime(["2026-10-05", "2026-10-06"] + (["2026-10-07"] if with_placeholder else []))
    d = {"A.NS": [100.0, 110.0] + ([None] if with_placeholder else []), "B.NS": [50.0, 60.0] + ([None] if with_placeholder else []),
         "C.NS": [10.0, 12.0] + ([None] if with_placeholder else [])}
    return pd.DataFrame(d, index=idx)


BHAV = pd.DataFrame({" SYMBOL": ["A", "B", "A"], " SERIES": [" EQ", " EQ", " BE"], " PREV_CLOSE": [110.0, 60.0, 5.0], " CLOSE_PRICE": [121.0, 57.0, 6.0]})


def test_new_row_uses_nse_daily_return():
    px, n = apply_bhav_returns(_px(), BHAV, "2026-10-07")
    assert n == 2 and px.index.max() == pd.Timestamp("2026-10-07")
    assert abs(px.loc["2026-10-07", "A.NS"] - 121.0) < 1e-9          # 110 * 121/110 (EQ series wins over BE)
    assert abs(px.loc["2026-10-07", "B.NS"] - 57.0) < 1e-9
    assert pd.isna(px.loc["2026-10-07", "C.NS"])                     # not in NSE file -> left empty


def test_fills_yahoo_placeholder_row_without_overwriting_real_values():
    px = _px(with_placeholder=True)
    px.loc["2026-10-07", "B.NS"] = 58.0                              # Yahoo already has a real value for B
    out, _ = apply_bhav_returns(px, BHAV, "2026-10-07")
    assert abs(out.loc["2026-10-07", "A.NS"] - 121.0) < 1e-9
    assert out.loc["2026-10-07", "B.NS"] == 58.0
