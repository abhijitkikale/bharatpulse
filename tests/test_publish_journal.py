import base64
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "pipeline"))
import publish_journal as P  # noqa: E402

IST = "Asia/Kolkata"


def test_a_day_is_published_once_after_15_40_or_on_the_next_start():
    t = lambda s: pd.Timestamp(s, tz=IST)
    assert not P.due(t("2026-11-20 15:39"), {}, "2026-11-20")                      # the session is not over yet
    assert P.due(t("2026-11-20 15:40"), {}, "2026-11-20")
    assert not P.due(t("2026-11-20 16:30"), {"day": "2026-11-20", "complete": True}, "2026-11-20")      # done: not again
    assert P.due(t("2026-11-21 09:00"), {"day": "2026-11-19", "complete": True}, "2026-11-20")           # missed yesterday: catch up on the next start
    assert not P.due(t("2026-11-20 16:00"), {}, None)


def test_publish_uploads_only_a_changed_page_with_the_right_body(monkeypatch):
    calls = []
    online = {"sha": "abc", "text": "old"}

    def fake(args, stdin=None, timeout=60):
        calls.append((args, stdin))
        if args[:2] == ["api", "-X"]:
            return 0, "{}", ""
        return 0, json.dumps({"sha": online["sha"], "content": base64.b64encode(online["text"].encode()).decode()}), ""
    monkeypatch.setattr(P, "_gh", fake)
    assert P.publish("same") == ("published", True)
    put = [c for c in calls if c[0][:2] == ["api", "-X"]][0]
    body = json.loads(put[1])
    assert put[0][2] == "PUT" and "contents/journal.html" in put[0][3] and body["sha"] == "abc" and body["branch"] == "main" and base64.b64decode(body["content"]).decode() == "same"
    online["text"] = "same"
    n = len(calls)
    assert P.publish("same") == ("already up to date", False) and len(calls) == n + 1          # only the read, no upload
