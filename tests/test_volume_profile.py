import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd

from compute_options import time_profile, volume_profile


def _bars(rows):
    idx = pd.bdate_range("2026-10-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["h", "l", "v"], index=idx)


def test_poc_sits_where_days_overlap():
    vp = volume_profile(_bars([(110, 100, 1000), (115, 105, 1000), (112, 104, 1000)]), 5, 1)
    assert 105 <= vp["poc"] <= 110 and vp["val"] <= vp["poc"] <= vp["vah"] and vp["src"] == "daily"


def test_value_area_holds_at_least_seventy_percent():
    vp = volume_profile(_bars([(110, 100, 1000), (115, 105, 1000), (112, 104, 1000), (109, 101, 1000)]), 5, 1)
    assert vp["share"] >= 70 and vp["days"] == 4


def test_window_is_respected_and_old_days_ignored():
    vp = volume_profile(_bars([(200, 190, 9999)] * 5 + [(110, 100, 1000), (111, 101, 1000), (112, 102, 1000)]), 3, 1)
    assert vp["days"] == 3 and 100 <= vp["val"] and vp["vah"] <= 112


def test_flat_day_and_too_little_data():
    assert volume_profile(_bars([(105, 105, 500)] * 3), 3, 1)["poc"] is not None
    assert volume_profile(_bars([(110, 100, 1000)]), 5, 1) is None


def _candles():
    """Two sessions of 5-minute index candles: day 1 around 100-110; day 2 (latest) mostly around 125, one excursion to 135."""
    rows = []
    for day, centre in (("2026-10-15", 105), ("2026-10-16", 125)):
        for i, t in enumerate(pd.date_range(f"{day} 09:15", periods=20, freq="5min", tz="Asia/Kolkata")):
            c = 135 if (day == "2026-10-16" and i == 19) else centre + (i % 3)
            rows.append((t, c + 1, c - 1))
    df = pd.DataFrame(rows, columns=["t", "h", "l"]).set_index("t")
    return df


def test_time_profile_poc_is_where_the_index_spent_its_time():
    p = time_profile(_candles(), 1, sessions=1)
    assert p["days"] == 1 and p["from"] == p["to"] == "2026-10-16" and p["src"] == "time"
    assert 124 <= p["poc"] <= 128                                         # the long stay near 125, not the one-candle excursion to 135
    assert p["val"] <= p["poc"] <= p["vah"] and p["vah"] < 133            # a single candle at 135 stays outside the value area
    assert p["val"] > 110                                                  # yesterday's 100-110 range does not leak in


def test_time_profile_needs_enough_sessions():
    assert time_profile(_candles(), 1, sessions=2)["days"] == 2
    assert time_profile(_candles(), 1, sessions=3) is None
