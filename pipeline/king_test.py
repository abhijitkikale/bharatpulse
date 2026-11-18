"""King trade, a TEST strategy shown only in the trading journal (it never touches the paper trades, Telegram or orders). Starts with the 19 Nov 2026 session.

Rules (the owner's, 18 Nov):
  * From 10:15 IST take a trade toward the King: CALL when the King is above price, PUT when it is below (at-the-money option, one lot).
  * Hold it until price reaches the King (a candle comes within APPROACH points of it); the exit is that candle's close.
  * If the King's direction turns against the trade (the King is now on the other side of price) and stays that way FLIP_MIN minutes, close the trade.
    Then wait until WAIT_AFTER_FLIP_MIN minutes after the turn began (5 minutes of confirmation + 5 of waiting) and take a trade toward the King's new direction.
    A turn that reverses within FLIP_MIN minutes is ignored.
  * After a trade that reached the King, wait WAIT_AFTER_TARGET_MIN minutes, then start the next one the same way (my call: the owner gave no rule for this case).
  * No new trade when price is already within MIN_DIST points of the King (my call), none from LAST_ENTRY; everything is closed at CLOSE (15:15 IST).
The simulation is deterministic from the nodes agent's saved readings (data/nodes/YYYY-MM-DD.jsonl) and the day's 1-minute index candles, so a restart changes nothing.
Option prices come from the same contract the paper trades would use (nearest expiry, at-the-money strike). Results go to data/king_test/YYYY-MM-DD.json; a finished day is frozen."""
import bisect
import datetime as dt
import json
import pathlib
import time

import pandas as pd

import nodes
import nodes_ledger

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIR = ROOT / "data" / "king_test"
NODES_DIR = ROOT / "data" / "nodes"
IST = "Asia/Kolkata"
FROM = "2026-11-19"                # the test starts with this session; earlier days are never created
START, LAST_ENTRY, CLOSE = (10, 15), (15, 10), (15, 15)
FLIP_MIN = 5                       # the King's direction must stay against the trade this long before the trade is closed
WAIT_AFTER_FLIP_MIN = 10           # a new trade starts this long after the turn began (5 to confirm, 5 to wait)
WAIT_AFTER_TARGET_MIN = 5
MIN_DIST = 25.0
APPROACH = 15.0
MAX_AGE_MIN = 10                   # a King reading older than this is not used
LOT = 65
REFRESH_OPEN_S = 180               # an open trade's option price is refreshed this often
RETRY_S = 300                      # a closed trade whose option candles could not be fetched is retried this often
REASONS = {"REACHED": "reached the King", "FLIPPED": "the King's direction changed for 5 minutes", "CLOSE": "closed at 15:15", "OPEN": "open"}


def _kings(readings):
    out = []
    for r in readings:
        try:
            k = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])["structure"].get("king")
        except Exception:
            k = None
        out.append((r["t"], k))
    return out


def _at(day, hm):
    return pd.Timestamp(f"{day} {hm[0]:02d}:{hm[1]:02d}", tz=IST)


def simulate(kings, candles, day):
    """kings: [(time, King strike)] from the saved readings; candles: the day's 1-minute index frame (o h l c). Returns the day's trades (Timestamps) in order."""
    if not kings or candles is None or len(candles) == 0:
        return []
    times = [t for t, _ in kings]
    start, last_entry, close = _at(day, START), _at(day, LAST_ENTRY), _at(day, CLOSE)

    def king_at(ts):
        i = bisect.bisect_right(times, ts) - 1
        if i < 0 or ts - times[i] > pd.Timedelta(minutes=MAX_AGE_MIN):
            return None
        return kings[i][1]

    trades, cur, ready, flip_start = [], None, start, None

    def finish(ts, px, status):
        cur.update(exit_time=ts, spot_out=px, status=status, pts=round(cur["dir"] * (px - cur["spot_in"]), 1))
        trades.append(cur)

    for ts, row in candles.iterrows():
        if ts < start:
            continue
        spot = float(row["c"])
        if ts >= close:
            if cur is not None:
                finish(ts, spot, "CLOSE")
                cur = None
            break
        k = king_at(ts)
        if cur is not None:
            if k is None:
                continue
            hi, lo = float(row["h"]), float(row["l"])
            if lo - APPROACH <= k <= hi + APPROACH:
                finish(ts, spot, "REACHED")
                cur, ready, flip_start = None, ts + pd.Timedelta(minutes=WAIT_AFTER_TARGET_MIN), None
                continue
            d = 1 if k > spot else -1
            if d != cur["dir"]:
                if flip_start is None:
                    flip_start = ts
                elif ts - flip_start >= pd.Timedelta(minutes=FLIP_MIN):
                    finish(ts, spot, "FLIPPED")
                    cur, ready, flip_start = None, flip_start + pd.Timedelta(minutes=WAIT_AFTER_FLIP_MIN), None
            else:
                flip_start = None
            continue
        if ts >= ready and ts < last_entry and k is not None and abs(k - spot) >= MIN_DIST:
            d = 1 if k > spot else -1
            cur = {"n": len(trades) + 1, "dir": d, "side": "CALL" if d > 0 else "PUT", "king": k, "entry_time": ts, "spot_in": spot, "strike": int(round(spot / 50.0) * 50),
                   "exit_time": None, "spot_out": None, "status": "OPEN", "pts": None}
            flip_start = None
    if cur is not None:                                       # still open on the last candle we have
        last = float(candles["c"].iloc[-1])
        cur.update(spot_out=last, pts=round(cur["dir"] * (last - cur["spot_in"]), 1))
        trades.append(cur)
    return trades


def _close_at(df, ts):
    i = bisect.bisect_right(list(df.index), ts) - 1
    return None if i < 0 else float(df["c"].iloc[i])


def price(trade, df):
    """Option prices for one trade from the contract's 1-minute candles: {opt_in, opt_out, pct, rs, candles}. Empty when there are no candles."""
    if df is None or len(df) == 0:
        return {}
    e = _close_at(df, trade["entry_time"])
    end = trade["exit_time"] if trade["exit_time"] is not None else df.index[-1]
    x = _close_at(df, end)
    if not e or x is None:
        return {}
    seg = df[(df.index >= trade["entry_time"]) & (df.index <= end)]
    return {"opt_in": round(e, 2), "opt_out": round(x, 2), "pct": round((x / e - 1) * 100, 1), "rs": round((x - e) * LOT),
            "candles": [[t.strftime("%H:%M"), round(float(r.o), 2), round(float(r.h), 2), round(float(r.l), 2), round(float(r.c), 2)] for t, r in seg.iterrows()]}


def _hm(ts):
    return None if ts is None else ts.strftime("%H:%M")


def to_doc(trades):
    out = []
    for t in trades:
        out.append({"n": t["n"], "side": t["side"], "strike": t["strike"], "king": t["king"], "entry_time": _hm(t["entry_time"]), "exit_time": _hm(t["exit_time"]),
                    "spot_in": round(t["spot_in"], 2), "spot_out": None if t["spot_out"] is None else round(t["spot_out"], 2), "pts": t["pts"], "status": t["status"],
                    "why": REASONS[t["status"]], **{k: v for k, v in t.items() if k in ("symbol", "expiry", "opt_in", "opt_out", "pct", "rs", "candles", "priced_at", "tried_at")}})
    return out


def load(day, folder=DIR):
    try:
        return json.loads((pathlib.Path(folder) / f"{day}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_all(folder=DIR):
    out = {}
    for f in sorted(pathlib.Path(folder).glob("????-??-??.json")):
        d = load(f.stem, folder)
        if d:
            out[f.stem] = d
    return out


def summary(docs):
    """Totals over every saved day: trades, winners, average option %, rupees for one lot."""
    tr = [t for d in docs.values() for t in d.get("trades", []) if t.get("pct") is not None]
    if not tr:
        return None
    return {"days": len(docs), "n": len(tr), "wins": sum(1 for t in tr if t["pct"] > 0), "avg_pct": round(sum(t["pct"] for t in tr) / len(tr), 1),
            "rs": sum(t["rs"] for t in tr), "pts": round(sum(t["pts"] for t in tr if t.get("pts") is not None), 1)}


_CACHE = {}


def update(day, index_df, contract_fn, folder=DIR, nodes_dir=NODES_DIR, now=None):
    """Recompute the day's King trades and price them. index_df: today's 1-minute index candles. contract_fn(day, (strike, 'CE'|'PE')) -> (symbol, expiry, option candles).
    Returns the saved document, or None (day before the test started, no readings, or nothing to do). Never raises."""
    try:
        day = str(day)
        if day < FROM:
            return None
        folder = pathlib.Path(folder)
        old = load(day, folder)
        if old and dt.date.fromisoformat(day) < dt.date.today():
            return old                                        # a finished day is frozen
        f = pathlib.Path(nodes_dir) / f"{day}.jsonl"
        if not f.exists():
            return old
        sig = (day, f.stat().st_size)                         # the King of each reading is worked out once per change of the saved readings
        if _CACHE.get("sig") != sig:
            _CACHE.update(sig=sig, kings=_kings(nodes_ledger.load_readings(f)))
        trades = simulate(_CACHE["kings"], index_df, day)
        if not trades and not old:
            return None
        prev = {(t["entry_time"], t["side"]): t for t in (old or {}).get("trades", [])}
        now_s = time.time() if now is None else now
        frames = {}
        for t in trades:
            p = prev.get((_hm(t["entry_time"]), t["side"]))
            done = p is not None and p.get("opt_out") is not None and t["status"] != "OPEN" and p.get("status") == t["status"] and p.get("exit_time") == _hm(t["exit_time"])
            if done:
                t.update({k: p[k] for k in ("symbol", "expiry", "opt_in", "opt_out", "pct", "rs", "candles", "priced_at") if k in p})
                continue
            if p is not None and t["status"] == "OPEN" and p.get("priced_at") and now_s - p["priced_at"] < REFRESH_OPEN_S:
                t.update({k: p[k] for k in ("symbol", "expiry", "opt_in", "opt_out", "pct", "rs", "candles", "priced_at") if k in p})
                continue
            if p is not None and p.get("tried_at") and now_s - p["tried_at"] < RETRY_S and p.get("opt_out") is None:
                t["tried_at"] = p["tried_at"]
                continue
            key = (float(t["strike"]), "CE" if t["side"] == "CALL" else "PE")
            if key not in frames:
                try:
                    frames[key] = contract_fn(day, key)
                except Exception:
                    frames[key] = (None, None, None)
            sym, exp, df = frames[key]
            got = price(t, df)
            if got:
                t.update(symbol=sym, expiry=exp, priced_at=now_s, **got)
            else:
                t.update(symbol=sym, expiry=exp, tried_at=now_s)
        doc = {"day": day, "rules": "King trade (test): toward the King from 10:15, until it is reached or its direction turns for 5 minutes, new trade 10 minutes after a turn, closed 15:15",
               "trades": to_doc(trades), "saved_at": dt.datetime.now().isoformat(timespec="seconds")}
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{day}.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc
    except Exception:
        return None
