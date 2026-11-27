import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import king_test as KT  # noqa: E402

IST = "Asia/Kolkata"
DAY = "2026-11-19"


def _candles(path, start="10:00"):
    """path: list of closes, one per minute from `start`."""
    idx = pd.date_range(f"{DAY} {start}", periods=len(path), freq="min", tz=IST)
    return pd.DataFrame([[c, c + 1, c - 1, c] for c in path], columns=["o", "h", "l", "c"], index=idx)


def _kings(spec):
    """spec: [(HH:MM, king)] -> readings list as simulate wants (time, king)."""
    return [(pd.Timestamp(f"{DAY} {hm}", tz=IST), k) for hm, k in spec]


def _every2(king_by_minute, start="10:00", n=330):
    ts = pd.date_range(f"{DAY} {start}", periods=n // 2, freq="2min", tz=IST)
    return [(t, king_by_minute(t)) for t in ts]


def test_first_trade_is_toward_the_king_after_10_15_and_ends_when_the_king_is_reached():
    path = [25000.0] * 15 + [25000.0 + 2 * i for i in range(0, 60)] + [25120.0] * 200       # flat, then up toward the King at 25100
    c = _candles(path)
    t = KT.simulate(_every2(lambda ts: 25100), c, DAY)
    assert t[0]["side"] == "CALL" and t[0]["entry_time"].strftime("%H:%M") == "10:15" and t[0]["king"] == 25100       # nothing before 10:15
    assert t[0]["status"] == "REACHED" and t[0]["pts"] > 60 and t[0]["exit_time"] > t[0]["entry_time"]


def test_after_reaching_the_king_it_waits_5_minutes_and_a_close_king_gives_no_trade():
    c = _candles([25000.0] * 16 + [25100.0] * 300)
    t = KT.simulate(_every2(lambda ts: 25100), c, DAY)                           # the first trade (10:15) reaches the King a minute later; later price sits on it: no new trade
    assert len(t) == 1 and t[0]["status"] == "REACHED"
    c2 = _candles([25000.0] * 15 + [25040.0] + [25000.0] * 300)
    king = lambda ts: 25100 if ts < pd.Timestamp(f"{DAY} 10:40", tz=IST) else 25000                     # a King right at price (0 points away) is not traded
    t2 = KT.simulate(_every2(king), c2, DAY)
    assert all(abs(x["king"] - x["spot_in"]) >= KT.MIN_DIST for x in t2)


def test_a_direction_turn_closes_the_trade_after_5_minutes_and_a_new_one_starts_10_minutes_after_the_turn():
    flat = [25000.0] * 400
    c = _candles(flat)
    # King 25100 (above) until 10:40, then 24900 (below) for good
    king = lambda ts: 25100 if ts < pd.Timestamp(f"{DAY} 10:40", tz=IST) else 24900
    t = KT.simulate(_every2(king), c, DAY)
    assert t[0]["side"] == "CALL" and t[0]["status"] == "FLIPPED"
    turn = pd.Timestamp(f"{DAY} 10:40", tz=IST)
    assert t[0]["exit_time"] - turn >= pd.Timedelta(minutes=5) and t[0]["exit_time"] - turn <= pd.Timedelta(minutes=7)
    assert t[1]["side"] == "PUT" and t[1]["entry_time"] >= turn + pd.Timedelta(minutes=10) and t[1]["entry_time"] <= turn + pd.Timedelta(minutes=12)


def test_a_turn_that_reverses_within_5_minutes_is_ignored():
    c = _candles([25000.0] * 400)
    def king(ts):
        m = ts.hour * 60 + ts.minute
        return 24900 if 10 * 60 + 40 <= m < 10 * 60 + 44 else 25100                # 4 minutes against, then back
    t = KT.simulate(_every2(king), c, DAY)
    assert len(t) == 1 and t[0]["status"] == "CLOSE" and t[0]["side"] == "CALL"


def test_everything_is_closed_at_15_15_and_nothing_starts_after_15_10():
    c = _candles([25000.0] * 400)
    t = KT.simulate(_every2(lambda ts: 25100), c, DAY)
    assert t[-1]["status"] == "CLOSE" and t[-1]["exit_time"].strftime("%H:%M") == "15:15"
    late = KT.simulate(_every2(lambda ts: 25100), _candles([25000.0] * 400, start="15:00"), DAY)   # candles only from 15:00 and the first trade only when 10:15 has passed
    assert all(x["entry_time"].strftime("%H:%M") < "15:10" for x in late)
    assert KT.simulate([], c, DAY) == [] and KT.simulate(_every2(lambda ts: 25100), None, DAY) == []


def test_an_open_trade_on_the_last_candle_stays_open():
    c = _candles([25000.0] * 40)                                                  # 10:00 to 10:39 only
    t = KT.simulate(_every2(lambda ts: 25100), c, DAY)
    assert t[0]["status"] == "OPEN" and t[0]["exit_time"] is None and t[0]["pts"] == 0.0


def _opt(prices, start="10:15"):
    idx = pd.date_range(f"{DAY} {start}", periods=len(prices), freq="min", tz=IST)
    return pd.DataFrame([[p, p + 1, p - 1, p] for p in prices], columns=["o", "h", "l", "c"], index=idx)


def test_option_pricing_and_the_saved_day(tmp_path):
    t = {"n": 1, "side": "CALL", "strike": 25000, "king": 25100, "entry_time": pd.Timestamp(f"{DAY} 10:15", tz=IST), "exit_time": pd.Timestamp(f"{DAY} 10:25", tz=IST),
         "spot_in": 25000.0, "spot_out": 25060.0, "status": "REACHED", "pts": 60.0, "dir": 1}
    p = KT.price(t, _opt([100.0] * 5 + [110.0] * 20))
    assert p["opt_in"] == 100.0 and p["opt_out"] == 110.0 and p["pct"] == 10.0 and p["rs"] == 650 and len(p["candles"]) == 11
    assert KT.price(t, None) == {}
    nd = tmp_path / "nodes"
    nd.mkdir()
    ts = pd.date_range(f"{DAY} 10:00", periods=90, freq="2min", tz=IST)
    pairs = [(24900, 5), (25000, 20), (25100, 300), (25200, 10)]
    (nd / f"{DAY}.jsonl").write_text("\n".join(json.dumps({"t": x.isoformat(), "spot": 25000.0, "k": [k for k, _ in pairs], "n": [v for _, v in pairs], "flip": 25000.0, "total_cr": 10.0}) for x in ts), encoding="utf-8")
    idx = pd.date_range(f"{DAY} 10:00", periods=170, freq="min", tz=IST)
    index_df = pd.DataFrame([[25000.0 + (i - 15) * 90.0 / 65 if 15 <= i < 80 else 25000.0 if i < 15 else 25090.0] * 4 for i in range(170)], columns=["o", "h", "l", "c"], index=idx)
    calls = []

    def contract(day, key):
        calls.append(key)
        return f"NSE:NIFTY26NOV{int(key[0])}{key[1]}", "2026-11-24", _opt([100.0] * 10 + [120.0] * 200)
    doc = KT.update(DAY, index_df, contract, folder=tmp_path / "kt", nodes_dir=nd, now=1000.0)
    assert doc and doc["trades"][0]["side"] == "CALL" and doc["trades"][0]["strike"] == 25000 and doc["trades"][0]["symbol"] == "NSE:NIFTY26NOV25000CE"
    first = doc["trades"][0]
    assert first["status"] == "REACHED" and first["opt_in"] == 100.0 and first["pct"] == 20.0 and first["rs"] == 1300
    n = len(calls)
    KT.update(DAY, index_df, contract, folder=tmp_path / "kt", nodes_dir=nd, now=1010.0)
    assert len(calls) == n or all(c for c in calls)                               # a closed, priced trade is not fetched again
    assert KT.update("2026-11-18", index_df, contract, folder=tmp_path / "kt", nodes_dir=nd) is None      # before the start day: nothing is created
    assert KT.load_all(tmp_path / "kt") and KT.summary(KT.load_all(tmp_path / "kt"))["n"] >= 1


def test_the_journal_shows_the_test_apart_from_the_paper_trades(tmp_path):
    import journal_dashboard
    jd, nd = tmp_path / "journal", tmp_path / "nodes"
    jd.mkdir()
    nd.mkdir()
    c = _candles([25000.0 + i for i in range(60)])
    (jd / f"{DAY}.json").write_text(json.dumps({"date": DAY, "symbol": "NIFTY", "total_pct": 0, "trades": [], "setups": [], "levels_used": {"levels": {}}, "saved_at": f"{DAY}T15:30:00",
                                                "index_candles_hm_ohlcv": [[t.strftime("%H:%M"), r.o, r.h, r.l, r.c, 0] for t, r in c.iterrows()]}), encoding="utf-8")
    kt = tmp_path / "kt"
    kt.mkdir()
    (kt / f"{DAY}.json").write_text(json.dumps({"day": DAY, "trades": [{"n": 1, "side": "CALL", "strike": 25000, "king": 25100, "entry_time": "10:15", "exit_time": "10:40", "spot_in": 25015.0,
                                                                         "spot_out": 25040.0, "pts": 25.0, "status": "REACHED", "why": "reached the King", "opt_in": 100.0, "opt_out": 110.0,
                                                                         "pct": 10.0, "rs": 650}]}), encoding="utf-8")
    html = journal_dashboard.build_html(jd, nd, tmp_path / "none.json", king_test_dir=kt)
    data = json.loads(html.split("const DATA = ")[1].split(";\nconst CAP")[0])
    assert data["days"][0]["ktest"]["trades"][0]["pct"] == 10.0 and data["ktest"]["rs"] == 650 and data["days"][0]["trades"] == []        # the paper-trade figures are untouched
    assert "function ktestHtml" in html and "function ktestPanel" in html and html.count("</script>") == 2
    none = journal_dashboard.build_html(jd, nd, tmp_path / "none.json", king_test_dir=tmp_path / "empty")
    assert json.loads(none.split("const DATA = ")[1].split(";\nconst CAP")[0])["ktest"] is None
