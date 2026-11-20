import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import journal_simple  # noqa: E402


def test_the_simple_journal_is_static_private_and_adds_up(tmp_path):
    jd = tmp_path / "journal"
    jd.mkdir()
    trades = [{"n": 1, "side": "CALL", "strike": 25000, "setup": "Now VWAP 25003.5 (bounce)", "entry_time": "09:40", "entry": 100.0, "exit_time": "09:50", "exit": 140.0, "result": "TARGET", "pnl_pct": 40.0},
              {"n": 2, "side": "PUT", "strike": 25000, "setup": "VAH 25100.00 (cross)", "entry_time": "10:10", "entry": 100.0, "exit_time": "10:12", "exit": 90.0, "result": "STOP", "pnl_pct": -10.0}]
    (jd / "2026-11-18.json").write_text(json.dumps({"date": "2026-11-18", "symbol": "NIFTY", "total_pct": 30, "trades": trades, "setups": [], "levels_used": {"levels": {}}, "index_candles_hm_ohlcv": [], "saved_at": "2026-11-18T15:30:00"}), encoding="utf-8")
    h = journal_simple.build_html(jd)
    assert "<script" not in h and "http" not in h.replace("https://", "")[:0] and "25,000" not in h                      # static, no capital shown
    assert "+&#8377;2,600" in h and "&minus;&#8377;650" in h and "+&#8377;1,950" in h                                       # 40 x 65, -10 x 65, net 30 x 65
    assert "Now VWAP" in h and "VAH" in h and "bounce" in h and "cross" in h
    out = journal_simple.write_file(tmp_path / "x" / "j.html", jd)
    assert out.exists() and out.stat().st_size < 60_000
