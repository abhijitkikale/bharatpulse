"""King pull scoreboard: does Nifty drift TOWARD the King node, more than it would toward any other level?

For every saved reading of the nodes agent (data/nodes/YYYY-MM-DD.jsonl) and each horizon (15, 30, 60 minutes) it asks, using that day's 1-minute index candles:
was price closer to the King HORIZON minutes later than it was at the reading, and by how many points; and did price touch the King within that time.
The same question is asked of control levels, because price that rotates inside a range keeps returning to anything in the middle of it:
  King +-50, +-100   the King moved sideways by 50 / 100 points (each direction counted as its own window)
  VWAP, day's range midpoint, nearest round hundred   other levels traders watch
A pull exists only if the King beats the mirror and the shifted levels by a clear margin over enough independent windows (readings 2 minutes apart overlap,
so the scoreboard also counts independent windows: distinct non-overlapping blocks of the horizon). Pure functions plus file helpers; no network."""
import bisect
import collections
import datetime as dt
import json
import pathlib

import pandas as pd

import nodes
import nodes_ledger

HORIZONS = (15, 30, 60)
MAX_AGE_MIN = 6
MIN_INDEPENDENT = 30          # fewer independent windows than this and the scoreboard says "too early"
MARGIN_PTS = 3.0              # the King must beat each control by this many points of average gain (and 5 percentage points of "closer")
LEVELS = ("king", "shift50", "shift100", "vwap", "mid", "round")
LABELS = {"king": "King", "shift50": "King shifted 50 points either way",
          "shift100": "King shifted 100 points either way", "vwap": "Day's VWAP", "mid": "Day's range midpoint", "round": "Nearest round hundred"}
EXPIRY_WEEKDAY = 1            # Tuesday (weekly Nifty expiry); a holiday that moves it is not modelled


def days_to_expiry(day):
    """Calendar days from the date to the next weekly expiry (0 on expiry day)."""
    d = dt.date.fromisoformat(day)
    return (EXPIRY_WEEKDAY - d.weekday()) % 7


def _expiry_bucket(day):
    n = days_to_expiry(day)
    return "expiry day" if n == 0 else "1 day before" if n == 1 else "2-3 days before" if n <= 3 else "4+ days before"


def _gain(l_value, s0, s1):
    """Points closer to the level at s1 than at s0 (negative = further away)."""
    return abs(s0 - l_value) - abs(s1 - l_value)


def samples_for_day(day, readings, candles, horizons=HORIZONS):
    """One sample per reading and horizon: {day, time, h, king, dist, sign, regime, bucket, expiry, gains:{level:[pts]}, touched:{level:[bool]}}.
    A level's lists hold one entry, or two for the shifted levels (one per direction)."""
    if not readings or candles is None or len(candles) < 3:
        return []
    times = [r["t"] for r in readings]
    idx = list(candles.index)
    closes = candles["c"].to_numpy(dtype=float)
    highs = candles["h"].to_numpy(dtype=float)
    lows = candles["l"].to_numpy(dtype=float)
    vol = candles["v"].to_numpy(dtype=float) if "v" in candles.columns else None
    typical = (highs + lows + closes) / 3.0
    cum_v = None if vol is None else vol.cumsum()
    cum_pv = None if vol is None else (typical * vol).cumsum()
    run_hi, run_lo = pd.Series(highs).cummax().to_numpy(), pd.Series(lows).cummin().to_numpy()
    out = []
    for r in readings:
        t = r["t"]
        if t.hour * 60 + t.minute < 9 * 60 + 20:
            continue
        i = bisect.bisect_right(idx, t) - 1
        if i < 0 or t - idx[i] > pd.Timedelta(minutes=MAX_AGE_MIN):
            continue
        cl = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])
        st = cl["structure"]
        king = st.get("king")
        if king is None:
            continue
        kn = [n for n in cl["nodes"] if "king" in n["roles"]]
        s0 = float(closes[i])
        levels = {"king": float(king),
                  "mid": float((run_hi[i] + run_lo[i]) / 2.0), "round": float(round(s0 / 100.0) * 100.0)}
        if cum_v is not None and cum_v[i] > 0:
            levels["vwap"] = float(cum_pv[i] / cum_v[i])
        for h in horizons:
            end = t + pd.Timedelta(minutes=h)
            if idx[-1] < end - pd.Timedelta(minutes=1):          # the day ended before this window did
                continue
            j = min(bisect.bisect_right(idx, end) - 1, len(idx) - 1)
            if j <= i:
                continue
            s1 = float(closes[j])
            seg_hi, seg_lo = float(highs[i + 1:j + 1].max()), float(lows[i + 1:j + 1].min())
            gains = {k: [_gain(v, s0, s1)] for k, v in levels.items()}
            touched = {k: [seg_lo <= v <= seg_hi] for k, v in levels.items()}
            for sh in (50, 100):                                  # shifted controls: both directions, each its own window
                for lv in (levels["king"] + sh, levels["king"] - sh):
                    gains.setdefault(f"shift{sh}", []).append(_gain(lv, s0, s1))
                    touched.setdefault(f"shift{sh}", []).append(seg_lo <= lv <= seg_hi)
            out.append({"day": day, "time": t.strftime("%H:%M"), "h": h, "king": king, "dist": round(float(king) - s0, 1),
                        "sign": kn[0]["sign"] if kn else "+", "regime": "POSITIVE" if r["total_cr"] > 0 else "NEGATIVE",
                        "bucket": nodes_ledger._bucket(t), "expiry": _expiry_bucket(day), "block": (t.hour * 60 + t.minute) // h, "gains": gains, "touched": touched})
    return out


def _stats(samples, level):
    g = [x for s in samples for x in s["gains"].get(level, [])]
    tc = [x for s in samples for x in s["touched"].get(level, [])]
    if not g:
        return None
    n = len(g)
    return {"level": level, "label": LABELS[level], "n": n, "closer": round(100.0 * sum(1 for x in g if x > 0) / n, 0),
            "gain": round(sum(g) / n, 1), "touched": round(100.0 * sum(tc) / n, 0)}


def _independent(samples):
    return len({(s["day"], s["block"]) for s in samples})


def _table(samples):
    rows = [_stats(samples, lv) for lv in LEVELS]
    return [r for r in rows if r]


def _distance_bucket(s):
    d = abs(s["dist"])
    return "King within 25 points" if d < 25 else "25 to 75 points away" if d < 75 else "75 to 150 points away" if d < 150 else "more than 150 points away"


def summarize(samples):
    """{"horizons": {h: {"n","independent","rows"}}, "splits": {title: [{label, n, independent, king_closer, king_gain, shift_closer, shift_gain}]}, "days", "verdict"} (splits at 30 minutes)."""
    res = {"horizons": {}, "splits": {}, "days": len({s["day"] for s in samples}), "verdict": ""}
    for h in HORIZONS:
        sub = [s for s in samples if s["h"] == h]
        res["horizons"][h] = {"n": len(sub), "independent": _independent(sub), "rows": _table(sub)}

    def split(title, keyfn, order):
        g = collections.defaultdict(list)
        for s in samples:
            if s["h"] == 30:
                g[keyfn(s)].append(s)
        rows = []
        for k in order:
            if k not in g:
                continue
            ks, ms = _stats(g[k], "king"), _stats(g[k], "shift50")
            rows.append({"label": k, "n": len(g[k]), "independent": _independent(g[k]), "king_closer": ks["closer"], "king_gain": ks["gain"],
                         "shift_closer": ms["closer"], "shift_gain": ms["gain"]})
        res["splits"][title] = rows
    split("How far the King is", _distance_bucket, ["King within 25 points", "25 to 75 points away", "75 to 150 points away", "more than 150 points away"])
    split("Days to the weekly expiry", lambda s: s["expiry"], ["expiry day", "1 day before", "2-3 days before", "4+ days before"])
    split("Regime", lambda s: s["regime"], ["POSITIVE", "NEGATIVE"])
    split("King sign", lambda s: "positive King" if s["sign"] == "+" else "negative King", ["positive King", "negative King"])
    split("Time of day", lambda s: s["bucket"], ["09:15-11:00", "11:00-13:00", "13:00-15:30"])
    res["verdict"] = _verdict(res["horizons"].get(30, {}), res["days"])
    return res


def _verdict(h30, days):
    rows = {r["level"]: r for r in h30.get("rows", [])}
    ind = h30.get("independent", 0)
    if "king" not in rows:
        return "No saved days with readings and candles yet."
    k = rows["king"]
    ctl = [rows[x] for x in ("shift50", "shift100", "vwap", "mid", "round") if x in rows]
    best = max(ctl, key=lambda r: r["gain"]) if ctl else None
    head = f"Over {days} day{'s' if days != 1 else ''} and about {ind} independent 30-minute windows: price got closer to the King {k['closer']:.0f}% of the time (average {k['gain']:+.1f} points)"
    if best:
        head += f"; the best control was {best['label']} at {best['closer']:.0f}% ({best['gain']:+.1f})."
    else:
        head += "."
    if ind < MIN_INDEPENDENT or not best:
        return head + f" Too early to say: at least {MIN_INDEPENDENT} independent windows are needed, and a day gives about 12."
    beats = all(k["gain"] >= c["gain"] + MARGIN_PTS and k["closer"] >= c["closer"] + 5 for c in ctl)
    if beats:
        return head + " The King beats every control, including the same King moved 50 and 100 points: a pull toward the King strike shows in this data."
    zone = k["gain"] >= rows["round"]["gain"] + MARGIN_PTS if "round" in rows else False
    if zone and k["closer"] > 55:
        return head + " Price does drift toward the King zone, but a level 50 points either side of it does about as well: the data cannot yet separate the exact strike from the zone around it."
    return head + " The King does no better than levels placed beside it or the usual references: no pull beyond price rotating inside its range."


def rebuild(nodes_dir, journal_dir):
    """All saved days -> list of samples."""
    out = []
    for f in sorted(pathlib.Path(nodes_dir).glob("????-??-??.jsonl")):
        out += samples_for_day(f.stem, nodes_ledger.load_readings(f), nodes_ledger.load_candles(f.stem, journal_dir))
    return out


def build_summary(nodes_dir, journal_dir):
    return summarize(rebuild(nodes_dir, journal_dir))


if __name__ == "__main__":
    root = pathlib.Path(__file__).resolve().parent.parent
    print(json.dumps(build_summary(root / "data" / "nodes", root / "data" / "journal"), indent=1))
