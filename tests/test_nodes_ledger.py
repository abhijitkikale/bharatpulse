import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import nodes_ledger as L  # noqa: E402

IST = "Asia/Kolkata"
DAY = "2026-11-13"
PAIRS = [(25000, -40), (25050, 5), (25100, 3), (25150, 4), (25200, 100), (25250, 6), (25300, 20)]      # significant: 25200 (King, +) and 25000 (-)


def _readings(total=100.0, start="09:20", n=40):
    ts = pd.date_range(f"{DAY} {start}", periods=n, freq="2min", tz=IST)
    return [{"t": t, "spot": 25100.0, "k": [k for k, _ in PAIRS], "n": [v for _, v in PAIRS], "flip": 25050.0, "total_cr": total} for t in ts]


def _candles(rows, start="09:20"):
    """rows: (high, low, close) per minute."""
    idx = pd.date_range(f"{DAY} {start}", periods=len(rows), freq="min", tz=IST)
    return pd.DataFrame([[c, h, l, c] for h, l, c in rows], columns=["o", "h", "l", "c"], index=idx)


def _walk(points, pad=3.0):
    return [(p + pad, p - pad, p) for p in points]


def test_a_bounce_off_a_node_is_held_and_a_break_through_is_passed():
    up = [25100, 25120, 25140, 25160, 25180, 25195, 25200, 25190, 25180, 25170, 25160, 25150, 25150]            # comes from below, touches 25200, falls back 40 points
    ev = [e for e in L.events_for_day(DAY, _readings(), _candles(_walk(up))) if e["k"] == 25200]
    assert len(ev) == 1 and ev[0]["outcome"] == "held" and ev[0]["from"] == "below" and ev[0]["role"] == "king" and ev[0]["sign"] == "+" and ev[0]["touches_before"] == 0
    assert ev[0]["regime"] == "POSITIVE" and ev[0]["bucket"] == "09:15-11:00" and ev[0]["down_pts"] >= 25
    thru = [25100, 25120, 25140, 25160, 25180, 25195, 25200, 25215, 25230, 25240, 25250]                       # goes straight through
    ev2 = [e for e in L.events_for_day(DAY, _readings(), _candles(_walk(thru))) if e["k"] == 25200]
    assert len(ev2) == 1 and ev2[0]["outcome"] == "passed" and ev2[0]["up_pts"] >= 25
    flat = [25100, 25150, 25190, 25195, 25200, 25205, 25200, 25195, 25205, 25200, 25198, 25202]               # hovers around the node: neither way by 25 points
    ev3 = [e for e in L.events_for_day(DAY, _readings(), _candles(_walk(flat, 2.0))) if e["k"] == 25200]
    assert len(ev3) == 1 and ev3[0]["outcome"] == "chop"


def test_events_are_debounced_until_price_moves_away_and_count_earlier_touches():
    path = [25100, 25150, 25195, 25200, 25185, 25190, 25200, 25140, 25120, 25100, 25080, 25110, 25150, 25195, 25200, 25160, 25140]
    ev = [e for e in L.events_for_day(DAY, _readings(), _candles(_walk(path))) if e["k"] == 25200]
    assert [e["touches_before"] for e in ev] == [0, 1] and [e["time"] for e in ev] == ["09:22", "09:33"]       # one event per visit; the second visit follows a move of 40+ points away


def test_no_reading_no_events_and_stale_readings_are_not_used():
    assert L.events_for_day(DAY, [], _candles(_walk([25100, 25200]))) == []
    stale = _readings(n=1, start="09:00")                                                                      # one reading, 20+ minutes older than the candles
    assert L.events_for_day(DAY, stale, _candles(_walk([25100, 25150, 25195, 25200]), start="09:30")) == []


def _ev(sign, outcome, pct=60.0, role="node", touches=0, regime="POSITIVE", v5=None, bucket="09:15-11:00", frm="above"):
    return {"day": DAY, "time": "10:00", "k": 25200, "pct": pct, "role": role, "sign": sign, "from": frm, "touches_before": touches, "regime": regime, "v5": v5,
            "bucket": bucket, "outcome": outcome, "up_pts": 0.0, "down_pts": 0.0}


def test_summary_rates_and_the_sign_check():
    evs = [_ev("+", "held")] * 8 + [_ev("+", "passed")] * 2 + [_ev("-", "held")] * 3 + [_ev("-", "passed")] * 7
    s = L.summarize(evs)
    assert s["overall"]["n"] == 20 and s["overall"]["held"] == 55.0
    rows = {r["label"]: r for r in s["groups"]["Node sign (dealer-sign check)"]}
    assert rows["positive nodes"]["held"] == 80.0 and rows["negative nodes"]["held"] == 30.0
    assert "positive nodes hold more often" in s["sign_check"]
    rev = L.summarize([_ev("+", "passed")] * 6 + [_ev("-", "held")] * 6)
    assert "may not fit" in rev["sign_check"]
    assert "not enough events" in L.summarize([_ev("+", "held")])["sign_check"]
    assert L.summarize([])["overall"]["n"] == 0


def test_rebuild_reads_saved_days_and_writes_the_csv(tmp_path):
    nd, jd = tmp_path / "nodes", tmp_path / "journal"
    nd.mkdir()
    jd.mkdir()
    rd = _readings()
    (nd / f"{DAY}.jsonl").write_text("\n".join(json.dumps({"t": r["t"].isoformat(), "spot": r["spot"], "k": r["k"], "n": r["n"], "flip": r["flip"], "total_cr": r["total_cr"]}) for r in rd), encoding="utf-8")
    c = _candles(_walk([25100, 25120, 25140, 25160, 25180, 25195, 25200, 25190, 25180, 25170, 25160, 25150, 25150]))
    (jd / f"{DAY}.json").write_text(json.dumps({"index_candles_hm_ohlcv": [[t.strftime("%H:%M"), r.o, r.h, r.l, r.c, 0] for t, r in c.iterrows()]}), encoding="utf-8")
    evs = L.rebuild(nd, jd)
    assert any(e["k"] == 25200 and e["outcome"] == "held" for e in evs)
    assert (nd / "ledger.csv").read_text(encoding="utf-8").splitlines()[0].startswith("day,time,k,pct")
    assert L.rebuild(tmp_path / "none", jd) == []


def test_replay_page_builds_from_saved_days_and_from_nothing(tmp_path):
    import nodes_replay
    nd, jd = tmp_path / "nodes", tmp_path / "journal"
    nd.mkdir()
    jd.mkdir()
    empty = nodes_replay.build_html(nd, jd)
    assert "No saved days yet" in empty and "const D=" in empty
    rd = _readings()
    (nd / f"{DAY}.jsonl").write_text("\n".join(json.dumps({"t": r["t"].isoformat(), "spot": r["spot"], "k": r["k"], "n": r["n"], "flip": r["flip"], "total_cr": r["total_cr"]}) for r in rd), encoding="utf-8")
    c = _candles(_walk([25100, 25120, 25140, 25160, 25180, 25195, 25200, 25190, 25180, 25170, 25160, 25150, 25150]))
    (jd / f"{DAY}.json").write_text(json.dumps({"index_candles_hm_ohlcv": [[t.strftime("%H:%M"), r.o, r.h, r.l, r.c, 0] for t, r in c.iterrows()]}), encoding="utf-8")
    data = nodes_replay.build_data(nd, jd)
    assert data["events"] >= 1 and data["days"][0]["date"] == DAY and data["days"][0]["events"][0]["outcome"] in ("held", "passed", "chop")
    html = nodes_replay.build_html(nd, jd)
    assert DAY in html and "</script>" in html and html.count("</script>") == 1
    out = nodes_replay.write_file(nd, jd, tmp_path / "x" / "replay.html")
    assert out.exists() and out.stat().st_size > 5000
