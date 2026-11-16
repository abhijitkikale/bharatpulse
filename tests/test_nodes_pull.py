import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import nodes_pull as P  # noqa: E402

IST = "Asia/Kolkata"
DAY = "2026-11-16"                      # a Monday: the weekly expiry is the next day
PAIRS = [(25000, -40), (25050, 5), (25100, 3), (25150, 4), (25200, 100), (25250, 6), (25300, 20)]       # the King is 25200


def _readings(n=60, start="09:20"):
    ts = pd.date_range(f"{DAY} {start}", periods=n, freq="2min", tz=IST)
    return [{"t": t, "spot": 25100.0, "k": [k for k, _ in PAIRS], "n": [v for _, v in PAIRS], "flip": 25050.0, "total_cr": 100.0} for t in ts]


def _candles(closes, start="09:20", vol=1000.0):
    idx = pd.date_range(f"{DAY} {start}", periods=len(closes), freq="min", tz=IST)
    return pd.DataFrame([[c, c + 2, c - 2, c, vol] for c in closes], columns=["o", "h", "l", "c", "v"], index=idx)


def test_price_walking_to_the_king_scores_as_closer_and_touched():
    closes = [25100 + 0.5 * i for i in range(0, 200)]                      # +0.5 point a minute toward 25200 (reached at minute 200)
    s = [x for x in P.samples_for_day(DAY, _readings(), _candles(closes)) if x["h"] == 30]
    assert s and all(x["gains"]["king"][0] > 0 for x in s) and all(x["king"] == 25200 for x in s)
    assert abs(s[0]["dist"] - 100) < 1
    away = [25100 - 0.5 * i for i in range(0, 200)]
    sa = [x for x in P.samples_for_day(DAY, _readings(), _candles(away)) if x["h"] == 30]
    assert all(x["gains"]["king"][0] < 0 for x in sa) and all(not x["touched"]["king"][0] for x in sa)


def test_shifted_levels_count_each_direction_and_the_day_must_cover_the_window():
    closes = [25100 + 0.5 * i for i in range(0, 200)]
    x = P.samples_for_day(DAY, _readings(), _candles(closes))[0]
    assert len(x["gains"]["shift50"]) == 2 and len(x["touched"]["shift100"]) == 2 and len(x["gains"]["king"]) == 1
    short = P.samples_for_day(DAY, _readings(n=60), _candles(closes[:25]))
    assert all(x["h"] == 15 for x in short) and short                       # a 30 or 60 minute window cannot be scored on 25 minutes of candles
    assert P.samples_for_day(DAY, [], _candles(closes)) == [] and P.samples_for_day(DAY, _readings(), None) == []


def test_summary_says_too_early_with_few_windows_and_judges_with_many():
    closes = [25100 + 0.5 * i for i in range(0, 200)]
    r = P.summarize(P.samples_for_day(DAY, _readings(), _candles(closes)))
    assert r["days"] == 1 and r["horizons"][30]["rows"][0]["level"] == "king" and r["horizons"][30]["rows"][0]["closer"] == 100.0
    assert "Too early" in r["verdict"] and r["splits"]["Days to the weekly expiry"][0]["label"] == "1 day before"

    def fake(level_gain, n=40):
        out = []
        for i in range(n):
            gains = {"king": [level_gain["king"]], "shift50": [level_gain["ctl"]] * 2, "shift100": [level_gain["ctl"]] * 2, "vwap": [level_gain["ctl"]], "mid": [level_gain["ctl"]], "round": [level_gain["ctl"]]}
            out.append({"day": f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}", "time": "10:00", "h": 30, "king": 25200, "dist": 50.0, "sign": "+", "regime": "POSITIVE", "bucket": "09:15-11:00",
                        "expiry": "expiry day", "block": 20, "gains": gains, "touched": {k: [True] * len(v) for k, v in gains.items()}})
        return out
    assert "a pull toward the King strike shows" in P.summarize(fake({"king": 20, "ctl": -5}))["verdict"]
    assert "no pull beyond" in P.summarize(fake({"king": 5, "ctl": 5}))["verdict"]


def test_days_to_expiry_and_the_replay_page_shows_the_scoreboard(tmp_path):
    assert P.days_to_expiry("2026-11-17") == 0 and P.days_to_expiry("2026-11-16") == 1 and P.days_to_expiry("2026-11-13") == 4
    import nodes_replay
    nd, jd = tmp_path / "nodes", tmp_path / "journal"
    nd.mkdir()
    jd.mkdir()
    rd = _readings()
    (nd / f"{DAY}.jsonl").write_text("\n".join(json.dumps({"t": r["t"].isoformat(), "spot": r["spot"], "k": r["k"], "n": r["n"], "flip": r["flip"], "total_cr": r["total_cr"]}) for r in rd), encoding="utf-8")
    c = _candles([25100 + 0.5 * i for i in range(200)])
    (jd / f"{DAY}.json").write_text(json.dumps({"index_candles_hm_ohlcv": [[t.strftime("%H:%M"), r.o, r.h, r.l, r.c, r.v] for t, r in c.iterrows()]}), encoding="utf-8")
    data = nodes_replay.build_data(nd, jd)
    assert data["pull"]["days"] == 1 and data["pull"]["horizons"][30]["n"] > 0
    html = nodes_replay.build_html(nd, jd)
    assert "King pull scoreboard" in html and html.count("</script>") == 1
    assert nodes_replay.build_data(tmp_path / "none", jd)["pull"]["days"] == 0
