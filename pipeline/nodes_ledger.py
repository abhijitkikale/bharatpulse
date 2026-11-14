"""Reaction ledger for the nodes agent (Phase 3, see docs/superpowers/specs/2026-11-14-nodes-agent-design.md).

For every saved day (data/nodes/YYYY-MM-DD.jsonl from the agent, with that day's 1-minute index candles from data/journal/YYYY-MM-DD.json) it finds each
time price APPROACHED a significant node (a candle range comes within APPROACH points of the strike, after price was away) and records what happened in the
next HORIZON minutes: price moved REACT points back the way it came ("held"), or REACT points through the node ("passed"), or neither ("chop").
Each event keeps the node's features at that time (strength, role, sign, fresh or touched before, regime, building or melting, time of day).
This is how "context = probability" turns into numbers: the summary tables give hit rates per feature, and the sign split is a check of the dealer
convention (positive nodes should hold more than negative ones if dealers really are long gamma there). Pure functions plus file helpers; no network."""
import bisect
import collections
import csv
import json
import pathlib

import pandas as pd

import nodes

APPROACH = 15.0        # a candle whose range comes this close to the strike is an approach
REARM = 40.0           # the node is armed again once a candle stays this far away
REACT = 25.0           # points either way that decide the outcome
HORIZON_MIN = 30
MAX_AGE_MIN = 6        # a reading older than this is not used for a candle
ROLE_ORDER = ("king", "gatekeeper", "ceiling", "floor")
FIELDS = ["day", "time", "k", "pct", "role", "sign", "from", "touches_before", "regime", "v5", "bucket", "outcome", "up_pts", "down_pts"]


def load_readings(path):
    """The agent's saved readings of one day -> list of dicts with a Timestamp t, sorted."""
    out = []
    try:
        for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            out.append({"t": pd.Timestamp(r["t"]), "spot": r["spot"], "k": r["k"], "n": r["n"], "flip": r.get("flip"), "total_cr": r.get("total_cr", 0.0)})
    except (OSError, ValueError):
        pass
    return sorted(out, key=lambda r: r["t"])


def load_candles(day, journal_dir):
    """The day's 1-minute index candles from the journal file, as a frame (o, h, l, c) with a tz-aware index, or None."""
    try:
        doc = json.loads((pathlib.Path(journal_dir) / f"{day}.json").read_text(encoding="utf-8"))
        rows = doc.get("index_candles_hm_ohlcv") or []
        if not rows:
            return None
        idx = [pd.Timestamp(f"{day} {r[0]}", tz="Asia/Kolkata") for r in rows]
        return pd.DataFrame([[r[1], r[2], r[3], r[4]] for r in rows], columns=["o", "h", "l", "c"], index=idx)
    except (OSError, ValueError):
        return None


def _bucket(ts):
    m = ts.hour * 60 + ts.minute
    return "09:15-11:00" if m < 660 else "11:00-13:00" if m < 780 else "13:00-15:30"


def _role(roles):
    for r in ROLE_ORDER:
        if r in roles:
            return r
    return "node"


def _outcome(candles, i, k, from_above):
    """held / passed / chop for the approach at candle i, scanning the next HORIZON_MIN minutes; also the best move each way."""
    end = candles.index[i] + pd.Timedelta(minutes=HORIZON_MIN)
    up = down = 0.0
    result = None
    for j in range(i + 1, len(candles)):
        if candles.index[j] > end:
            break
        hi, lo = float(candles["h"].iloc[j]), float(candles["l"].iloc[j])
        up, down = max(up, hi - k), max(down, k - lo)
        if result is None:
            up_hit, dn_hit = hi >= k + REACT, lo <= k - REACT
            if up_hit and dn_hit:
                result = "chop"
            elif up_hit:
                result = "held" if from_above else "passed"
            elif dn_hit:
                result = "passed" if from_above else "held"
    return result or "chop", round(up, 1), round(down, 1)


def events_for_day(day, readings, candles):
    """The approach events of one day (list of dicts with FIELDS)."""
    if not readings or candles is None or len(candles) == 0:
        return []
    times = [r["t"] for r in readings]
    cache = {}

    def reading_at(ts):
        i = bisect.bisect_right(times, ts) - 1
        if i < 0 or ts - times[i] > pd.Timedelta(minutes=MAX_AGE_MIN):
            return None, None
        if i not in cache:
            r = readings[i]
            cache[i] = nodes.classify({"k": r["k"], "net": r["n"]}, r["spot"])["nodes"]
        return i, cache[i]

    def v5(i, k):
        r = readings[i]
        j = bisect.bisect_right(times, r["t"] - pd.Timedelta(minutes=5)) - 1
        if j < 0:
            return None
        now, then = dict(zip(r["k"], r["n"])), dict(zip(readings[j]["k"], readings[j]["n"]))
        king = max((abs(v) for v in r["n"]), default=0)
        if not king or k not in then:
            return None
        return round((abs(now.get(k, 0.0)) - abs(then[k])) / king * 100, 1)

    state, seen, out = {}, collections.Counter(), []
    closes = candles["c"].to_numpy(dtype=float)
    for i, (ts, row) in enumerate(candles.iterrows()):
        ri, nds = reading_at(ts)
        if ri is None:
            continue
        hi, lo = float(row["h"]), float(row["l"])
        for n in nds:
            k = n["k"]
            near = lo <= k + APPROACH and hi >= k - APPROACH
            st = state.get(k, "out")
            if st == "out" and near:
                state[k] = "in"
                prev = closes[i - 1] if i > 0 else float(row["o"])
                from_above = prev > k
                res, up, down = _outcome(candles, i, k, from_above)
                vel = v5(ri, k)
                out.append({"day": day, "time": ts.strftime("%H:%M"), "k": k, "pct": n["pct"], "role": _role(n["roles"]), "sign": n["sign"],
                            "from": "above" if from_above else "below", "touches_before": seen[k], "regime": "POSITIVE" if readings[ri]["total_cr"] > 0 else "NEGATIVE",
                            "v5": vel, "bucket": _bucket(ts), "outcome": res, "up_pts": up, "down_pts": down})
                seen[k] += 1
            elif st == "in" and (lo > k + REARM or hi < k - REARM):
                state[k] = "out"
    return out


def rebuild(nodes_dir, journal_dir, write_csv=True):
    """All saved days -> list of events (and data/nodes/ledger.csv)."""
    nodes_dir = pathlib.Path(nodes_dir)
    events = []
    for f in sorted(nodes_dir.glob("????-??-??.jsonl")):
        events += events_for_day(f.stem, load_readings(f), load_candles(f.stem, journal_dir))
    if write_csv and events:
        with open(nodes_dir / "ledger.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(events)
    return events


def _row(label, evs):
    n = len(evs)
    c = collections.Counter(e["outcome"] for e in evs)
    pct = lambda x: round(100.0 * x / n, 0) if n else None
    return {"label": label, "n": n, "held": pct(c["held"]), "passed": pct(c["passed"]), "chop": pct(c["chop"])}


def summarize(events):
    """Hit rates per feature: {"overall": row, "groups": {title: [rows]}, "sign_check": text}. held / passed / chop in % of the events of that group."""
    def by(title, keyfn, order=None):
        g = collections.defaultdict(list)
        for e in events:
            g[keyfn(e)].append(e)
        keys = [k for k in (order or sorted(g, key=str)) if k in g]
        return title, [_row(str(k), g[k]) for k in keys]

    strength = lambda e: "25-50%" if e["pct"] < 50 else "50-75%" if e["pct"] < 75 else "75-100%"
    touches = lambda e: "fresh (0 before)" if e["touches_before"] == 0 else "1 before" if e["touches_before"] == 1 else "2+ before"
    vel = lambda e: "no reading" if e["v5"] is None else "building (+3 or more)" if e["v5"] >= 3 else "melting (-3 or less)" if e["v5"] <= -3 else "steady"
    groups = dict([
        by("Node sign (dealer-sign check)", lambda e: "positive nodes" if e["sign"] == "+" else "negative nodes"),
        by("Role", lambda e: e["role"], ["king", "gatekeeper", "ceiling", "floor", "node"]),
        by("Strength (% of King)", strength, ["25-50%", "50-75%", "75-100%"]),
        by("Touched before today", touches, ["fresh (0 before)", "1 before", "2+ before"]),
        by("Regime", lambda e: e["regime"], ["POSITIVE", "NEGATIVE"]),
        by("Building or melting", vel, ["building (+3 or more)", "steady", "melting (-3 or less)", "no reading"]),
        by("Time of day", lambda e: e["bucket"], ["09:15-11:00", "11:00-13:00", "13:00-15:30"]),
        by("Came from", lambda e: e["from"], ["above", "below"]),
    ])
    pos = [e for e in events if e["sign"] == "+"]
    neg = [e for e in events if e["sign"] == "-"]
    hr = lambda L: (sum(1 for e in L if e["outcome"] == "held") / len(L) * 100) if L else None
    if len(pos) >= 5 and len(neg) >= 5:
        hp, hn = hr(pos), hr(neg)
        verdict = ("positive nodes hold more often than negative ones, as the convention (dealers long gamma at positive nodes) predicts" if hp > hn + 5 else
                   "negative nodes hold as often or more than positive ones: the convention may not fit Nifty" if hn >= hp else "no clear difference yet")
        sign_check = f"held: positive nodes {hp:.0f}% of {len(pos)}, negative nodes {hn:.0f}% of {len(neg)}: {verdict}."
    else:
        sign_check = "not enough events yet to compare positive and negative nodes."
    return {"overall": _row("all approaches", events), "groups": groups, "sign_check": sign_check}
