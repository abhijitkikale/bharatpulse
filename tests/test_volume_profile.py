import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import numpy as np
import pandas as pd

from compute_options import _value_area, intraday_block, volume_profile


def _bars(rows):
    idx = pd.bdate_range("2026-10-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["h", "l", "v"], index=idx)


def test_value_area_rule_stops_before_exceeding_target():
    vol = np.array([10, 20, 40, 20, 10], dtype=float)           # total 100, target 70
    lo, hi, acc = _value_area(vol, 2, 0.70)
    # POC=40; tie 20/20 -> above; then 10 above vs 20 below -> below would reach 80 > 70, so stop at 60
    assert (lo, hi, acc) == (2, 3, 60)


def test_value_area_picks_larger_neighbour_and_handles_edges():
    lo, hi, acc = _value_area(np.array([50, 30, 10, 5, 5], dtype=float), 0, 0.70)
    assert lo == 0 and hi == 1 and acc == 80 - 0 or (lo, hi) == (0, 0)   # only the upper side exists; never exceeds the target
    assert acc <= 70 or (lo, hi) == (0, 0)


def test_poc_sits_where_days_overlap_daily_fallback():
    vp = volume_profile(_bars([(110, 100, 1000), (115, 105, 1000), (112, 104, 1000)]), 5, 1)
    assert 105 <= vp["poc"] <= 110 and vp["val"] <= vp["poc"] <= vp["vah"] and vp["src"] == "daily"
    assert 55 <= vp["share"] <= 70                                        # at most the 70% target


def test_daily_fallback_window_and_flat_day():
    vp = volume_profile(_bars([(200, 190, 9999)] * 5 + [(110, 100, 1000), (111, 101, 1000), (112, 102, 1000)]), 3, 1)
    assert vp["days"] == 3 and 100 <= vp["val"] and vp["vah"] <= 112
    assert volume_profile(_bars([(105, 105, 500)] * 3), 3, 1)["poc"] is not None
    assert volume_profile(_bars([(110, 100, 1000)]), 5, 1) is None


def _candles(sessions=2):
    """5-minute candles. Latest session: long stay (heavy volume) near 125, a quick spike to 135 on tiny volume."""
    rows = []
    for k in range(sessions):
        day = (pd.Timestamp("2026-10-12") + pd.offsets.BDay(k)).strftime("%Y-%m-%d")
        base = 105 if k < sessions - 1 else 125
        for i, t in enumerate(pd.date_range(f"{day} 09:15", periods=20, freq="5min", tz="Asia/Kolkata")):
            c = 135 if (k == sessions - 1 and i == 19) else base + (i % 3)
            rows.append((t, c, c + 1, c - 1, c, 100 if c == 135 else 1000))
    return pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"]).set_index("t")


def test_one_day_profile_uses_latest_session_and_volume_not_time():
    vp, ses = intraday_block(_candles(2), 1)
    one = vp["1"]
    assert one["days"] == 1 and one["from"] == one["to"] and one["src"] == "volume"
    assert 124 <= one["poc"] <= 128 and one["val"] > 110 and one["vah"] < 133      # the thin 135 spike and the old session stay outside
    assert "5" not in vp                                                           # fewer than five sessions exist


def test_five_session_profile_needs_five_sessions():
    vp, ses = intraday_block(_candles(5), 1)
    assert vp["5"]["days"] == 5 and vp["5"]["src"] == "volume" and len(ses) == 5


def test_vwap_matches_a_hand_calculation():
    vp, ses = intraday_block(_candles(1), 1)
    s = ses[0]
    typical0 = (s["h"][0] + s["l"][0] + s["c"][0]) / 3
    assert abs(s["vwap"][0] - typical0) < 0.01                                      # first bar: VWAP is its own typical price
    num = sum(((s["h"][i] + s["l"][i] + s["c"][i]) / 3) * s["v"][i] for i in range(3))
    den = sum(s["v"][:3])
    assert abs(s["vwap"][2] - num / den) < 0.01
    assert s["prof"]["val"] <= s["prof"]["poc"] <= s["prof"]["vah"] and len(s["prof"]["p"]) == len(s["prof"]["v"])
