import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import journal_dashboard  # noqa: E402
import king_journal as K  # noqa: E402

IST = "Asia/Kolkata"
DAY = "2026-11-17"
PAIRS = [(25000, -40), (25050, 5), (25100, 3), (25150, 4), (25200, 100), (25250, 6), (25300, 20)]       # the King is 25200


def _readings(n=110, start="09:20"):
    ts = pd.date_range(f"{DAY} {start}", periods=n, freq="2min", tz=IST)
    return [{"t": t, "spot": 25100.0, "k": [k for k, _ in PAIRS], "n": [v for _, v in PAIRS], "flip": 25050.0, "total_cr": 100.0} for t in ts]


def _candles(closes, start="09:20"):
    idx = pd.date_range(f"{DAY} {start}", periods=len(closes), freq="min", tz=IST)
    return pd.DataFrame([[c, c + 2, c - 2, c, 1000.0] for c in closes], columns=["o", "h", "l", "c", "v"], index=idx)


def test_the_kings_candle_is_the_one_that_reaches_it_and_the_day_is_summarised():
    closes = [25100 + 0.5 * i for i in range(0, 200)]                  # walks up to the King (25200 at minute 200)
    a = K.day_analysis(DAY, _readings(), _candles(closes), [{"n": 1, "side": "CALL", "entry_time": "09:30", "pnl_pct": 20.0}, {"n": 2, "side": "PUT", "entry_time": "09:40", "pnl_pct": -10.0}])
    assert a and a["runs"][0]["king"] == 25200 and a["runs"][0]["candle"]["reached"] is True
    assert a["runs"][0]["candle"]["from"] == "below" and abs(a["runs"][0]["candle"]["h"] - 25200) < 40
    assert [t["with_king"] for t in a["trades"]] == [True, False]       # a call with the King above price points toward it; a put points away
    assert any(l.startswith("Trades by direction") for l in a["lines"]) and any("Drift toward the King" in l for l in a["lines"])
    assert a["map"][0][2] == 25200 and len(a["map"][0]) == 7 and isinstance(a["events"], list)             # the rows the journal draws its King chart from
    assert a["close"]["king"] == 25200 and a["pull"]["30"]["king"]["closer"] == 100.0 and a["king_steps"][0][2] == 25200
    far = K.day_analysis(DAY, _readings(), _candles([25000 - 0.1 * i for i in range(200)]), [])
    assert far["runs"][0]["candle"]["reached"] is False and far["runs"][0]["candle"]["closest"] > 15 and "did not come within 15 points" in " ".join(far["lines"])
    assert K.day_analysis(DAY, [], _candles(closes)) is None and K.day_analysis(DAY, _readings(), None) is None


def test_the_last_minutes_of_the_day_and_flickers_are_not_analysed():
    rd = _readings(n=110)
    for r in rd[-3:]:                                                  # a nonsense King at the very end (the options have expired)
        r["k"], r["n"] = [26000, 25100], [500.0, 1.0]
    a = K.day_analysis(DAY, rd, _candles([25100.0] * 150))             # candles end 09:20 + 149 min = 11:49; the last readings are after 11:44
    assert a["close"]["king"] == 25200 and all(r["king"] != 26000 for r in a["runs"])
    flick = _readings(n=110)
    flick[10]["k"], flick[10]["n"] = [25000, 25100], [900.0, 1.0]       # one reading with another King
    b = K.day_analysis(DAY, flick, _candles([25100.0] * 160))
    assert b["flickers"] >= 1 and all(r["readings"] >= K.MIN_RUN for r in b["runs"])


def test_the_journal_page_carries_the_king_analysis_and_your_notes(tmp_path):
    nd, jd = tmp_path / "nodes", tmp_path / "journal"
    nd.mkdir()
    jd.mkdir()
    rd = _readings()
    (nd / f"{DAY}.jsonl").write_text("\n".join(json.dumps({"t": r["t"].isoformat(), "spot": r["spot"], "k": r["k"], "n": r["n"], "flip": r["flip"], "total_cr": r["total_cr"]}) for r in rd), encoding="utf-8")
    c = _candles([25100 + 0.5 * i for i in range(200)])
    doc = {"date": DAY, "symbol": "NIFTY", "total_pct": 0, "trades": [], "setups": [], "levels_used": {"levels": {}}, "saved_at": f"{DAY}T15:30:00",
           "index_candles_hm_ohlcv": [[t.strftime("%H:%M"), r.o, r.h, r.l, r.c, r.v] for t, r in c.iterrows()]}
    (jd / f"{DAY}.json").write_text(json.dumps(doc), encoding="utf-8")
    notes = tmp_path / "notes.json"
    notes.write_text(json.dumps({DAY: "my note about the King"}), encoding="utf-8")
    html = journal_dashboard.build_html(jd, nd, notes)
    assert "function kingChart" in html and "King analysis" in html and "my note about the King" in html and "king_steps" in html and html.count("</script>") == 2          # the chart library tag and the page script
    data = json.loads(html.split("const DATA = ")[1].split(";\nconst CAP")[0])
    assert data["days"][0]["king"]["runs"][0]["king"] == 25200 and data["days"][0]["notes"] == ["my note about the King"] and data["pull"]["days"] == 1
    plain = journal_dashboard.build_html(jd, tmp_path / "none", tmp_path / "none.json")                 # no nodes data and no notes: the journal still builds
    assert "const DATA" in plain and json.loads(plain.split("const DATA = ")[1].split(";\nconst CAP")[0])["days"][0]["king"] is None


def test_regime_bands_follow_the_live_flip_and_the_side_price_is_on(tmp_path):
    reg = tmp_path / "reg"
    reg.mkdir()
    (reg / f"{DAY}.json").write_text(json.dumps([{"time": f"{DAY}T09:10:00+05:30", "flip": 25100.0, "pos_above": True, "pos": True},
                                                 {"time": f"{DAY}T10:00:00+05:30", "flip": 25100.0, "pos_above": False, "pos": False}]), encoding="utf-8")
    candles = [["09:30", 25210, 25190, 25200], ["09:31", 25210, 25190, 25205], ["09:32", 25110, 25090, 25095], ["10:00", 25210, 25190, 25200], ["10:01", 25110, 25090, 25095]]
    bands = journal_dashboard.regime_bands(DAY, candles, reg)
    assert bands == [["09:30", "09:31", "P"], ["09:32", "10:00", "N"], ["10:01", "10:01", "P"]]       # the 10:00 reading flips which side is positive, so the same price is now negative
    assert journal_dashboard.regime_bands(DAY, candles, tmp_path / "none") == []


def test_the_journal_has_a_results_by_level_table_with_rupees(tmp_path):
    jd = tmp_path / "journal"
    jd.mkdir()
    (jd / f"{DAY}.json").write_text(json.dumps({"date": DAY, "symbol": "NIFTY", "total_pct": 0, "trades": [], "setups": [], "levels_used": {"levels": {}}, "saved_at": f"{DAY}T15:30:00", "index_candles_hm_ohlcv": []}), encoding="utf-8")
    html = journal_dashboard.build_html(jd, tmp_path / "none", tmp_path / "none.json")
    assert 'id="bylevel"' in html and "function levelHtml" in html and "Winners total" in html and "Losers total" in html and "$('bylevel').innerHTML = levelHtml(days)" in html
