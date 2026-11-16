"""The local mailbox between the hidden TradingView browser and the stream page (data/tv_inbox.jsonl).

tools/tv_live_paste.py reads TradingView's own alert log (the same list the Alerts panel shows) and appends every new BP Live message here, one JSON line each:
{"fire_id", "bar_time", "message", "http"}. The stream page reads the new lines at every step and treats each message exactly like one that arrived through the
ntfy relay (opening candles and setups), so a rejected relay message (HTTP 429) no longer loses anything. Pure helpers around one small file."""
import json
import pathlib

INBOX = pathlib.Path(__file__).resolve().parent.parent / "data" / "tv_inbox.jsonl"


def append(rows, path=None):
    """Add rows (dicts) to the mailbox."""
    if not rows:
        return 0
    p = pathlib.Path(path or INBOX)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return len(rows)


def read_new(offset, path=None):
    """(messages, new_offset): the message texts of the lines added since `offset` (a byte position; 0 = from the start). A half-written last line is left for next time."""
    p = pathlib.Path(path or INBOX)
    try:
        with open(p, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    out = []
    for line in data[:end + 1].splitlines():
        try:
            m = json.loads(line.decode("utf-8")).get("message")
        except ValueError:
            continue
        if m:
            out.append(m)
    return out, offset + end + 1
