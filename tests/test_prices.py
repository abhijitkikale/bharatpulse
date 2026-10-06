import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd

from fetch import merge_prices


def _frame(rows):
    return pd.DataFrame(rows).set_index("d").rename_axis(None)


def test_stale_download_does_not_wipe_newer_cached_rows():
    old = _frame([{"d": pd.Timestamp("2026-09-30"), "A": 10.0}, {"d": pd.Timestamp("2026-10-01"), "A": 11.0},
                  {"d": pd.Timestamp("2026-10-05"), "A": 12.0}])
    stale_new = _frame([{"d": pd.Timestamp("2026-09-30"), "A": 10.0}, {"d": pd.Timestamp("2026-10-01"), "A": 11.0}])
    merged = merge_prices(old, stale_new)
    assert merged.index.max() == pd.Timestamp("2026-10-05") and merged.loc["2026-10-05", "A"] == 12.0


def test_fresh_rows_are_added_and_override():
    old = _frame([{"d": pd.Timestamp("2026-10-01"), "A": 11.0}])
    new = _frame([{"d": pd.Timestamp("2026-10-01"), "A": 11.5}, {"d": pd.Timestamp("2026-10-05"), "A": 12.0}])
    merged = merge_prices(old, new)
    assert list(merged["A"]) == [11.5, 12.0]


def test_empty_download_keeps_cache():
    old = _frame([{"d": pd.Timestamp("2026-10-01"), "A": 11.0}])
    assert merge_prices(old, pd.DataFrame()).equals(old)
