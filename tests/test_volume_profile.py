import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd

from compute_options import intraday_profiles, volume_profile


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


def _intraday():
    """Two sessions of 5-minute bars. Day 1 trades around 100-110; day 2 (the latest) trades 120-130 with most volume near 125."""
    rows = []
    for day, base in (("2026-10-15", 100), ("2026-10-16", 120)):
        ts = pd.date_range(f"{day} 09:15", periods=12, freq="5min", tz="Asia/Kolkata")
        for i, t in enumerate(ts):
            lo = base + (i % 6) * 1.5
            rows.append((t, lo + 2, lo, 5000 if (day == "2026-10-16" and 3 <= i <= 6) else 1000))
    df = pd.DataFrame(rows, columns=["t", "h", "l", "v"]).set_index("t")
    return df[["h", "l"]], df["v"]


def test_one_day_profile_uses_only_the_latest_session():
    idx, vol = _intraday()
    out = intraday_profiles(idx, vol, 1)
    one = out["1"]
    assert one["days"] == 1 and one["from"] == one["to"] == "2026-10-16" and one["src"] == "intraday"
    assert 120 <= one["val"] <= one["poc"] <= one["vah"] <= 130           # nothing from the 100-110 session leaks in


def test_multi_session_profile_spans_sessions_and_missing_sessions_give_none():
    rows = []
    for day, base in (("2026-10-15", 100), ("2026-10-16", 120)):                 # equal volume, disjoint price ranges
        for i, t in enumerate(pd.date_range(f"{day} 09:15", periods=12, freq="5min", tz="Asia/Kolkata")):
            lo = base + (i % 6) * 1.5
            rows.append((t, lo + 2, lo, 1000))
    df = pd.DataFrame(rows, columns=["t", "h", "l", "v"]).set_index("t")
    out = intraday_profiles(df[["h", "l"]], df["v"], 1, windows=(1, 2, 5))
    assert out["2"]["days"] == 2 and out["2"]["val"] < 110 and out["2"]["vah"] > 120    # the 70% area must reach both sessions
    assert out["1"]["val"] >= 120                                                       # one session alone stays in its own range
    assert out["5"] is None                                                             # only two sessions exist
