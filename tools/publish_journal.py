"""Publish the simple journal (pipeline/journal_simple.py) to the public site: https://abhijitkikale.github.io/bharatpulse/journal.html

How: the page is written to journal.html in the GitHub repository with the GitHub API (the `gh` command, already signed in on this PC), so no local git commit, push or rebase is
involved and your working folder is untouched. The evening cloud build (.github/workflows/refresh.yml) copies journal.html into the site.
Only the simple page goes up: results, no notes, charts, King analysis or capital.

  python tools/publish_journal.py          publish now (does nothing when the page has not changed)
  python tools/publish_journal.py --check  show what would be published (no upload)

The stream server (tools/stream_server.py) calls maybe_publish() once a trading day after 15:40 IST, and once more on the next start-up if that day was missed."""
import base64
import json
import pathlib
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
REPO, PATH, BRANCH = "abhijitkikale/bharatpulse", "journal.html", "main"
STATE = ROOT / "data" / "journal_published.json"        # {"day": last journal day published, "complete": bool, "at": ISO time}
AFTER = (15, 40)                                        # IST: the session's last trade is closed and the journal is final
_lock = threading.Lock()


def _gh(args, stdin=None, timeout=60):
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, timeout=timeout, encoding="utf-8")
    return r.returncode, r.stdout, r.stderr


def remote():
    """(sha, text) of journal.html on the site's repository, or (None, None) when it is not there yet."""
    code, out, _ = _gh(["api", f"repos/{REPO}/contents/{PATH}?ref={BRANCH}"])
    if code != 0:
        return None, None
    j = json.loads(out)
    return j["sha"], base64.b64decode(j["content"]).decode("utf-8")


def publish(html_text=None):
    """(status text, changed?). Uploads only when the page differs from what is online."""
    import journal_simple
    text = html_text if html_text is not None else journal_simple.build_html()
    sha, old = remote()
    if old == text:
        return "already up to date", False
    body = {"message": "Update public journal", "content": base64.b64encode(text.encode("utf-8")).decode("ascii"), "branch": BRANCH}
    if sha:
        body["sha"] = sha
    code, out, err = _gh(["api", "-X", "PUT", f"repos/{REPO}/contents/{PATH}", "--input", "-"], stdin=json.dumps(body))
    if code != 0:
        return "upload failed: " + (err or out)[:200], False
    return "published", True


def _last_day():
    try:
        import journal_dashboard
        cfg = journal_dashboard.load_settings()
        days = sorted(p.stem for p in journal_dashboard.FOLDER.glob("????-??-??.json") if p.stem >= (cfg.get("from") or ""))
        return days[-1] if days else None
    except Exception:
        return None


def due(now, state, last_day):
    """True when the newest journal day is final (the session is over, or it is a later date) and what is online is not that final version yet."""
    if not last_day:
        return False
    final = str(now.date()) > last_day or (now.date().isoformat() == last_day and (now.hour, now.minute) >= AFTER)
    return final and not (state.get("day") == last_day and state.get("complete"))


def maybe_publish(now):
    """Called from the stream server's cycle with the current IST time. Runs in a background thread and never raises."""
    try:
        last = _last_day()
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            state = {}
        if not due(now, state, last) or not _lock.acquire(blocking=False):
            return

        def run():
            try:
                msg, changed = publish()
                if msg in ("published", "already up to date"):
                    STATE.write_text(json.dumps({"day": last, "complete": True, "at": now.isoformat(), "result": msg}), encoding="utf-8")
            except Exception:
                pass
            finally:
                _lock.release()
        threading.Thread(target=run, daemon=True, name="publish-journal").start()
    except Exception:
        pass


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--check" in sys.argv:
        import journal_simple
        t = journal_simple.build_html()
        sha, old = remote()
        print("page", len(t), "bytes; online:", "none" if old is None else ("same" if old == t else "different"))
        return
    print(publish()[0])


if __name__ == "__main__":
    main()
