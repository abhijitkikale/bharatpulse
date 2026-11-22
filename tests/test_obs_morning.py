import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pipeline"))
import obs_control as O  # noqa: E402


def test_youtube_refusals_logged_after_the_start_are_found(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    f = d / "2026-11-20 06-44-38.txt"
    f.write_text("06:44:38.891: old line\n06:44:40.000: YouTube API error:\n06:44:40.001: \tHTTP status: 403\n", encoding="utf-8")
    monkeypatch.setattr(O, "OBS_LOGS", d)
    mark = O.log_mark()
    assert mark[0] == f and mark[1] == f.stat().st_size
    assert O.youtube_errors_since(mark) == []                                         # the error before the mark does not count
    with f.open("a", encoding="utf-8") as fh:
        fh.write("06:45:19.960: YouTube API error:\n06:45:19.960: \tHTTP status: 403\n06:45:19.960: \tURL: https://www.googleapis.com/youtube/v3/videos?part=snippet\n")
    errs = O.youtube_errors_since(mark)
    assert len(errs) == 1 and "403" in errs[0] and "videos" in errs[0]
    monkeypatch.setattr(O, "OBS_LOGS", tmp_path / "missing")
    assert O.log_mark() == (None, 0) and O.youtube_errors_since((None, 0)) == []        # no OBS logs: no crash


def test_the_account_fix_message_names_the_cause_and_the_remedy():
    assert "Disconnect Account" in O.ACCOUNT_FIX and "403" in O.ACCOUNT_FIX and "stream key" in O.ACCOUNT_FIX
