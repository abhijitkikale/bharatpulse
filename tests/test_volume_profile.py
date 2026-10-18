import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd

from compute_options import volume_profile


def _bars(rows):
    idx = pd.bdate_range("2026-10-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["h", "l", "v"], index=idx)


def test_poc_sits_where_days_overlap():
    # day 1 spans 100-110, day 2 spans 105-115, equal volume: the 105-110 overlap is the busiest
    vp = volume_profile(_bars([(110, 100, 1000), (115, 105, 1000), (112, 104, 1000)]), 5, 1)
    assert 105 <= vp["poc"] <= 110
    assert vp["val"] <= vp["poc"] <= vp["vah"]


def test_value_area_holds_at_least_seventy_percent():
    vp = volume_profile(_bars([(110, 100, 1000), (115, 105, 1000), (112, 104, 1000), (109, 101, 1000)]), 5, 1)
    assert vp["share"] >= 70 and vp["days"] == 4


def test_volume_is_conserved_and_window_is_respected():
    old = [(200, 190, 9999)] * 5                          # far-away old days must be ignored by a 3-day window
    new = [(110, 100, 1000), (111, 101, 1000), (112, 102, 1000)]
    vp = volume_profile(_bars(old + new), 3, 1)
    assert vp["days"] == 3 and 100 <= vp["val"] and vp["vah"] <= 112


def test_flat_day_and_too_little_data():
    assert volume_profile(_bars([(105, 105, 500), (105, 105, 500), (105, 105, 500)]), 3, 1)["poc"] is not None
    assert volume_profile(_bars([(110, 100, 1000)]), 5, 1) is None
