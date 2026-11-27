import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import expiry_week as E  # noqa: E402
import stream_levels  # noqa: E402

IST = "Asia/Kolkata"


def test_a_wednesday_starts_a_cycle_and_tuesday_ends_it():
    assert E.expiry_of("2026-11-25") == "2026-12-01"      # Wednesday
    assert E.expiry_of("2026-11-27") == "2026-12-01"      # Friday
    assert E.expiry_of("2026-11-30") == "2026-12-01"      # Monday
    assert E.expiry_of("2026-12-01") == "2026-12-01"      # Tuesday itself
    assert E.expiry_of("2026-12-02") == "2026-12-08"      # next Wednesday: the next cycle


def test_vwap_and_bands_match_the_stream_page_and_restart_daily():
    rows = [[f"09:{15 + i}", 100 + i, 102 + i, 99 + i, 101 + 2 * i, 1000 + 50 * i] for i in range(12)]
    got = E.vwap_series(rows)
    idx = pd.date_range("2026-11-27 09:15", periods=12, freq="min", tz=IST)
    df = pd.DataFrame([[r[1], r[2], r[3], r[4], r[5]] for r in rows], columns=["o", "h", "l", "c", "v"], index=idx)
    up, lo = stream_levels.vwap_bands(df)
    daily, _ = stream_levels.vwaps(df)
    for g, u, l_, d in zip(got, up, lo, daily):
        assert abs(g[0] - d) < 0.01 and abs(g[1] - u) < 0.01 and abs(g[2] - l_) < 0.01
    again = E.vwap_series(rows)                          # a new day starts from scratch: the same rows give the same numbers
    assert again == got


def test_candles_without_volume_do_not_break_the_vwap():
    rows = [["09:15", 10, 11, 9, 10, 0], ["09:16", 10, 12, 10, 11, 500], ["09:17", 11, 11, 11, 11, 0]]
    got = E.vwap_series(rows)
    assert got[0] is None and got[1][0] == round((12 + 10 + 11) / 3, 2) and got[2][0] == got[1][0]


def test_node_series_gives_the_kings_size_sign_and_total():
    ks = [25000, 25050, 25100]
    ns = [-40.0, 120.0, 10.0]
    r = [{"t": pd.Timestamp("2026-11-27 09:20", tz=IST), "spot": 25040.0, "k": ks, "n": ns, "flip": 25000.0, "total_cr": 90.0}]
    out = E.node_series(r)
    assert out == [["09:20", 120.0, "+", 90.0]]


def test_journal_page_carries_the_expiry_week_tab_and_per_day_series(tmp_path):
    import json
    import journal_dashboard as J
    day = "2026-11-25"
    rows = [[f"09:{15 + i}", 100 + i, 102 + i, 99 + i, 101 + i, 1000] for i in range(5)]
    (tmp_path / f"{day}.json").write_text(json.dumps({"date": day, "symbol": "NIFTY", "trades": [], "setups": [], "index_candles_hm_ohlcv": rows}), encoding="utf-8")
    days = J.load_days(tmp_path)
    assert days[0]["exp"] == "2026-12-01" and len(days[0]["vw"]) == 5 and days[0]["candles"][0][4] == 100      # the open is the 5th value of a candle
    html = J.build_html(folder=tmp_path, nodes_dir=tmp_path / "none", notes=tmp_path / "n.json", king_test_dir=tmp_path / "kt", regime_dir=tmp_path / "reg")
    assert 'id="navweek"' in html and 'id="weekview"' in html and "function weekView" in html


def test_weekly_vwap_matches_the_stream_page_and_restarts_on_monday():
    rows1 = [[f"09:{15 + i}", 100 + i, 102 + i, 99 + i, 101 + i, 1000 + 10 * i] for i in range(6)]
    rows2 = [[f"09:{15 + i}", 110 + i, 112 + i, 109 + i, 111 + i, 900] for i in range(6)]
    got = E.weekly_vwap([("2026-11-26", rows1), ("2026-11-27", rows2), ("2026-11-30", rows1)])        # Thu, Fri (same ISO week), Mon (new week)
    idx = pd.DatetimeIndex([pd.Timestamp(f"{d} {r[0]}", tz=IST) for d, rr in (("2026-11-26", rows1), ("2026-11-27", rows2), ("2026-11-30", rows1)) for r in rr])
    df = pd.DataFrame([[r[1], r[2], r[3], r[4], r[5]] for rr in (rows1, rows2, rows1) for r in rr], columns=["o", "h", "l", "c", "v"], index=idx)
    _, weekly = stream_levels.vwaps(df)
    flat = got["2026-11-26"] + got["2026-11-27"] + got["2026-11-30"]
    for g, w in zip(flat, weekly):
        assert abs(g - w) < 0.01
    assert got["2026-11-30"][0] == round((102 + 99 + 101) / 3, 2)                                # Monday starts from scratch


def test_node_history_rows_are_the_live_charts_bubble_rows():
    ks = [25000, 25050, 25100]
    r = [{"t": pd.Timestamp("2026-11-27 09:20", tz=IST), "spot": 25040.0, "k": ks, "n": [-40.0, 120.0, 10.0], "flip": 25000.0, "total_cr": 90.0}]
    out = E.node_history(r)
    assert out[0][0] == int(r[0]["t"].timestamp()) + 19800
    king = [x for x in out[0][1] if x[3] == 1][0]
    assert king[0] == 25050 and king[1] == 100 and king[2] == 1 and king[4].startswith("K")
