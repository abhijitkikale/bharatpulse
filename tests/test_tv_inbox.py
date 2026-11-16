import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "tools"))
import stream_levels  # noqa: E402
import tv_inbox  # noqa: E402
import tv_live_paste as tv  # noqa: E402

SIG = '{"k":"sig","t":1794822180000,"s":"CALL","l":"GEX call wall","v":25200,"g":" (bounce)","c":25206.3}'
BAR = '{"t":1794801540000,"o":25215.4,"h":25231.85,"l":25208.65,"c":25231.85,"v":1128745}'


class FakePage:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def evaluate(self, js, args):
        self.calls.append(args)
        return {"s": "ok", "r": self.rows}


def _fire(fid, msg, name="BP Live (2, 0.25): Any alert() function call", code=429, when="2026-11-16T09:44:02Z"):
    return {"fire_id": fid, "name": name, "message": msg, "bar_time": when, "fire_time": when, "webhook": {"http_code": code}}


def test_messages_the_relay_rejected_still_reach_the_mailbox_once(tmp_path, monkeypatch):
    monkeypatch.setattr(tv_inbox, "INBOX", tmp_path / "inbox.jsonl")
    page = FakePage([_fire(2, BAR, when="2026-11-16T04:00:02Z"), _fire(1, SIG, code=200), _fire(3, "x", name="Some other alert"), _fire(4, None)])
    seen = set()
    assert tv.poll_fires(page, seen) == 2 and seen == {"1", "2", "3", "4"}                  # only BP Live messages with text; the other alert and the empty one are skipped
    assert page.calls[0][0] == "list_fires" and page.calls[0][1] == {"payload": {"limit": 80}}
    assert tv.poll_fires(page, seen) == 0                                                    # the same log again: nothing is handed over twice
    msgs, off = tv_inbox.read_new(0)
    assert msgs == [BAR, SIG]                                                                # oldest first
    assert tv_inbox.read_new(off) == ([], off)
    page.rows = [_fire(9, SIG.replace("CALL", "PUT"), when="2026-11-16T09:50:00Z")]
    assert tv.poll_fires(page, seen) == 1
    more, off2 = tv_inbox.read_new(off)
    assert len(more) == 1 and '"PUT"' in more[0] and off2 > off


def test_the_mailbox_leaves_a_half_written_line_and_the_page_reads_the_real_messages(tmp_path):
    p = tmp_path / "m.jsonl"
    tv_inbox.append([{"message": SIG}], p)
    with open(p, "ab") as f:
        f.write(b'{"message":"par')                                                          # a line still being written
    msgs, off = tv_inbox.read_new(0, p)
    assert msgs == [SIG] and tv_inbox.read_new(off, p) == ([], off)
    with open(p, "ab") as f:
        f.write(b'tial"}\n')
    assert tv_inbox.read_new(off, p)[0] == ["partial"]
    assert tv_inbox.read_new(0, tmp_path / "none.jsonl") == ([], 0)
    day = pd.Timestamp("2026-11-16", tz="Asia/Kolkata").date()
    sg = stream_levels.parse_signal(SIG, day)
    assert sg and sg["side"] == "CALL" and sg["time"].strftime("%H:%M") == "15:13" and "GEX call wall" in sg["level"]
    bar = stream_levels.parse_open_bar(BAR, day, max_days=4)
    assert bar and bar[0].strftime("%H:%M") == "09:29" and bar[1]["c"] == 25231.85


def test_inbox_summary_counts_setups_and_opening_candles_of_one_day(tmp_path):
    p = tmp_path / "m.jsonl"
    tv_inbox.append([{"bar_time": "2026-11-16T04:01:00Z", "message": SIG}, {"bar_time": "2026-11-16T03:45:00Z", "message": BAR}, {"bar_time": "2026-11-16T03:46:00Z", "message": BAR},
                     {"bar_time": "2026-11-13T04:01:00Z", "message": SIG}], p)
    assert tv_inbox.summary("2026-11-16", p) == (1, 2) and tv_inbox.summary("2026-11-13", p) == (1, 0) and tv_inbox.summary("2026-11-17", p) == (0, 0)
    assert tv_inbox.summary("2026-11-16", tmp_path / "none.jsonl") == (0, 0)
