"""Nodes for Nifty (the Heatseeker / Atlas ideas, see docs/superpowers/specs/2026-11-14-nodes-agent-design.md).

Pure functions, no network: chain rows in, a small JSON-ready snapshot out. tools/nodes_agent.py feeds them every minute.

A "node" is one strike of one expiry with its dealer gamma exposure (calls +, puts -, rupee crore per 1% move, from gex.compute_gex). The
Front column (nearest expiry) is ranked: King = largest |value|, strength % = |value| / |King|, significant = at least SIG_PCT. All thresholds
are first guesses, to be calibrated on our own data later (the owner reads "context, not a trigger" as probability)."""
import collections
import re

import numpy as np
import pandas as pd

import gex
import live_gex

N_EXP = 4              # expiry columns kept
SIG_PCT = 25.0         # a node is significant from this share of the King
AIR_PCT = 10.0         # strikes under this share of the King form an air pocket (3 or more in a row)
TOUCH_PTS = 5.0        # price "touches" a node inside strike +/- this
BACK_PTS = 30.0        # a backing node sits this close to the setup level on the protected side
ROOM_OK, ROOM_BLOCK = 50.0, 25.0
NEAR_NODE_PTS = 40.0   # no significant node this close either side of the level = "in the middle of nowhere"
GRID_STRIKES = 20      # strikes each side of spot written to the snapshot
LATE_MIN = 3           # a grade is marked late when the snapshot is more than this many minutes newer than the setup


def columns(chain, now, n_exp=N_EXP):
    """chain: {"spot", "rows"} (fetch_fyers.fetch_chains). -> list of columns, nearest expiry first:
    {"expiry", "days", "flip", "total_cr", "k": [...], "net": [...], "call": [...], "put": [...], "straddle"}."""
    spot = float(chain["spot"])
    rows = chain["rows"]
    today = now.date()
    out = []
    for e in sorted({r["XpryDt"] for r in rows})[:n_exp]:
        opts = live_gex._opts(rows, spot, e, today)
        trade_date = today - pd.Timedelta(days=1) if pd.Timestamp(e).date() <= today else today      # expiry day: the model runs with one day left
        try:
            g = gex.compute_gex(opts, str(trade_date), spot, max_expiries=1, surface_expiries=1)
        except Exception:
            g = None
        if not g or not g.get("nearest"):
            continue
        n = g["nearest"]
        ce = {r["StrkPric"]: r["ltp"] for r in rows if r["XpryDt"] == e and r["OptnTp"] == "CE" and r["ltp"] > 0}
        pe = {r["StrkPric"]: r["ltp"] for r in rows if r["XpryDt"] == e and r["OptnTp"] == "PE" and r["ltp"] > 0}
        both = sorted(set(ce) & set(pe), key=lambda k: abs(k - spot))
        out.append({"expiry": e, "days": (pd.Timestamp(e).date() - today).days, "flip": n["flip"], "total_cr": n["total_cr"], "k": list(n["k"]), "atm_iv": (g.get("atm_iv") or {}).get(e),
                    "net": list(n["net"]), "call": list(n["call"]), "put": list(n["put"]),
                    "straddle": round(ce[both[0]] + pe[both[0]], 2) if both else None})
    return out


def touches(candles, k, upto=None):
    """How many separate times the 1-minute candles (columns h, l) entered k +/- TOUCH_PTS (until `upto`, a Timestamp, when given)."""
    if candles is None or len(candles) == 0:
        return 0
    c = candles if upto is None else candles[candles.index < upto]
    if len(c) == 0:
        return 0
    inside = ((c["l"] <= k + TOUCH_PTS) & (c["h"] >= k - TOUCH_PTS)).to_numpy()
    return int(inside[0]) + int(np.sum(inside[1:] & ~inside[:-1]))


def velocity(hist, now, ks, nets, minutes):
    """{strike: percentage points of the King gained (+) or lost (-) over `minutes`} from the buffer hist (deque of (Timestamp, {strike: value})),
    or {} when the buffer does not reach back that far."""
    then = None
    for ts, snap in hist:
        if ts <= now - pd.Timedelta(minutes=minutes):
            then = snap
    king = max((abs(v) for v in nets), default=0)
    if then is None or not king:
        return {}
    return {k: round((abs(v) - abs(then.get(k, 0.0))) / king * 100, 1) for k, v in zip(ks, nets) if k in then}


def classify(col, spot, candles=None, exp_move=None):
    """Roles and structure of one column. Returns {"nodes": [significant nodes], "structure": {...}}; nodes carry k, net, pct, roles, touches, fresh."""
    ks, net = col["k"], col["net"]
    if not ks:
        return {"nodes": [], "structure": {"king": None}}
    absn = np.abs(np.array(net, dtype=float))
    kmax = float(absn.max())
    if kmax <= 0:
        return {"nodes": [], "structure": {"king": None}}
    pct = absn / kmax * 100
    ki = int(absn.argmax())
    king = ks[ki]
    sig = [i for i in range(len(ks)) if pct[i] >= SIG_PCT]
    roles = collections.defaultdict(list)
    roles[ki].append("king")
    below = [i for i in sig if ks[i] < spot]
    above = [i for i in sig if ks[i] > spot]
    floor = max(below, key=lambda i: ks[i]) if below else None          # the range edges are the NEAREST significant nodes
    ceil = min(above, key=lambda i: ks[i]) if above else None
    if floor is not None:
        roles[floor].append("floor")
    if ceil is not None:
        roles[ceil].append("ceiling")
    lo, hi = sorted((spot, king))
    between = [i for i in sig if lo < ks[i] < hi and i != ki]
    gate = max(between, key=lambda i: absn[i]) if between else None
    if gate is not None:
        roles[gate].append("gatekeeper")
    for sign, name in ((1, "pika"), (-1, "barney")):
        pool = sorted((i for i in sig if i != ki and (net[i] > 0) == (sign > 0)), key=lambda i: -absn[i])[:2]
        for i in pool:
            roles[i].append(name)
    # air pockets: 3 or more strikes in a row under AIR_PCT of the King, near spot
    near = [i for i in range(len(ks)) if abs(ks[i] - spot) <= 1000]
    pockets, run = [], []
    for i in near:
        if pct[i] < AIR_PCT:
            run.append(i)
        else:
            if len(run) >= 3:
                pockets.append([ks[run[0]], ks[run[-1]]])
            run = []
    if len(run) >= 3:
        pockets.append([ks[run[0]], ks[run[-1]]])
    midpoint, pos = False, None
    if floor is not None and ceil is not None and ks[ceil] > ks[floor]:
        rng = ks[ceil] - ks[floor]
        pos = round((spot - ks[floor]) / rng, 2)
        midpoint = rng >= 100 and 0.35 <= pos <= 0.65
    nodes = []
    for i in sig:
        t = touches(candles, ks[i])
        flags = []
        if i == ki and exp_move and abs(king - spot) > 1.5 * exp_move:
            flags.append("decoy")
        nodes.append({"k": ks[i], "net": round(float(net[i]), 2), "pct": round(float(pct[i]), 1), "sign": "+" if net[i] > 0 else "-",
                      "roles": roles.get(i, []), "flags": flags, "touches": t, "fresh": t == 0})
    nodes.sort(key=lambda n: -n["pct"])
    return {"nodes": nodes, "structure": {"king": king, "floor": ks[floor] if floor is not None else None, "ceiling": ks[ceil] if ceil is not None else None,
                                          "gatekeeper": ks[gate] if gate is not None else None, "air_pockets": pockets, "midpoint": midpoint, "range_pos": pos}}


def regime(col, spot):
    """{"now": POSITIVE/NEGATIVE, "side": -1 below / +1 above the live flip / 0 unknown, "flip"}: the sign of the Front column's total gamma at spot."""
    flip = col.get("flip")
    return {"now": "POSITIVE" if col["total_cr"] > 0 else "NEGATIVE", "side": 0 if flip is None else (1 if spot > flip else -1), "flip": flip}


def expected_move(spot, ref, iv_pct):
    """The one-day move the options price (1 sigma = reference price x IV x sqrt(1/365)), as fixed levels around a reference (the previous close), and how much of it
    the day has used so far ("pace", in sigma). None when there is no volatility or reference. A fixed band, as Atlas draws it: price can reach and break it."""
    if not iv_pct or not ref:
        return None
    sig = float(ref) * float(iv_pct) / 100.0 * (1.0 / 365.0) ** 0.5
    return {"basis": "previous close", "ref": round(float(ref), 2), "iv": round(float(iv_pct), 2), "sigma": round(sig, 1),
            "up1": round(ref + sig, 1), "dn1": round(ref - sig, 1), "up2": round(ref + 2 * sig, 1), "dn2": round(ref - 2 * sig, 1),
            "pace": round((float(spot) - float(ref)) / sig, 2)}


def _first_number(text):
    m = re.search(r"\d+(?:\.\d+)?", text or "")
    return float(m.group(0)) if m else None


def setup_key(day, when, side, level):
    return f"{day} {when} {side} {re.sub(r'[0-9.]+', '', level or '').strip()}"


def grade_setup(setup, snap, levels, candles, day, now):
    """setup: {"when": "HH:MM", "side", "level": text}; snap: this module's snapshot; levels: the page's [{"name", "value"}] list; candles: today's 1-minute
    frame. -> {"grade", "score", "reasons", "late"} or None when the level cannot be read."""
    L = _first_number(setup.get("level"))
    if L is None or not snap.get("nodes"):
        return None
    side = setup["side"]
    nodes = snap["nodes"]
    score, why, flags = 0, [], []
    # 1 backing node on the protected side
    prot = [n for n in nodes if (L - BACK_PTS <= n["k"] <= L + 10)] if side == "CALL" else [n for n in nodes if (L - 10 <= n["k"] <= L + BACK_PTS)]
    back = max(prot, key=lambda n: n["pct"]) if prot else None
    if back:
        pts = 2 if back["pct"] >= 60 else 1
        score += pts
        why.append(f"backed by the {back['k']:.0f} node ({back['pct']:.0f}% of King{', ' + back['roles'][0] if back['roles'] else ''}) {pts:+d}")
    # 2 room to the next node on the target side
    ahead = sorted((n for n in nodes if (n["k"] > L if side == "CALL" else n["k"] < L)), key=lambda n: abs(n["k"] - L))
    if ahead:
        d = abs(ahead[0]["k"] - L)
        if d >= ROOM_OK:
            score += 1
            why.append(f"room: next node {ahead[0]['k']:.0f} is {d:.0f} points away +1")
        elif d < ROOM_BLOCK and ahead[0]["pct"] >= 40:
            score -= 1
            flags.append("blocked")
            why.append(f"blocked: node {ahead[0]['k']:.0f} ({ahead[0]['pct']:.0f}%) only {d:.0f} points away -1")
    else:
        score += 1
        why.append("room: no node ahead +1")
    # 3 freshness of the backing node (before the setup)
    if back is not None and candles is not None and len(candles):
        t0 = pd.Timestamp(f"{day} {setup['when']}", tz="Asia/Kolkata")
        n_t = touches(candles, back["k"], upto=t0)
        if n_t == 0:
            score += 1
            why.append("backing node fresh (not touched before) +1")
        elif n_t >= 3:
            score -= 1
            why.append(f"backing node touched {n_t} times before -1")
    # 4 confluence with other levels
    nm = re.sub(r"[0-9.]+", "", setup.get("level") or "").split("(")[0].strip().lower()
    others = [lv for lv in (levels or []) if abs(float(lv["value"]) - L) <= 10 and lv["name"].lower() != nm and nm not in lv["name"].lower()]
    if len(others) >= 2:
        score += 1
        why.append("confluence: " + ", ".join(lv["name"] for lv in others[:3]) + " +1")
    # 5 regime
    rg = snap.get("regime") or {}
    if rg.get("now") == "NEGATIVE" and rg.get("side"):
        with_move = (rg["side"] < 0 and side == "PUT") or (rg["side"] > 0 and side == "CALL")
        score += 1 if with_move else -2
        if not with_move:
            flags.append("contra")
        why.append("negative gamma, " + ("with the move +1" if with_move else "against the move -2"))
    # 5b in the middle of the range between the floor and the ceiling
    stc = snap.get("structure") or {}
    if stc.get("midpoint") and stc.get("floor") is not None and stc.get("ceiling") is not None and stc["floor"] <= L <= stc["ceiling"]:
        score -= 1
        flags.append("midpoint")
        why.append(f"in the middle of the range {stc['floor']:.0f}-{stc['ceiling']:.0f} -1")
    # 6 nothing nearby
    if not any(abs(n["k"] - L) <= NEAR_NODE_PTS for n in nodes):
        score -= 1
        flags.append("nonode")
        why.append(f"no significant node within {NEAR_NODE_PTS:.0f} points -1")
    grade = "A" if score >= 3 else "B" if score >= 1 else "C"
    late = False
    try:
        late = (now - pd.Timestamp(f"{day} {setup['when']}", tz="Asia/Kolkata")) > pd.Timedelta(minutes=LATE_MIN)
    except Exception:
        pass
    return {"grade": grade, "score": score, "reasons": why, "flags": flags, "late": late}


def build_snapshot(chain, now, candles=None, hist=None, prev_close=None):
    """The agent's snapshot of one Nifty chain (no grades yet). hist: deque of (Timestamp, {strike: Front value}) for velocity."""
    spot = float(chain["spot"])
    cols = columns(chain, now)
    if not cols:
        return None
    front = cols[0]
    exp_move = round(0.8 * front["straddle"], 1) if front.get("straddle") else None
    cl = classify(front, spot, candles, exp_move)
    v5 = velocity(hist or [], now, front["k"], front["net"], 5)
    v15 = velocity(hist or [], now, front["k"], front["net"], 15)
    for n in cl["nodes"]:
        n["v5"], n["v15"] = v5.get(n["k"]), v15.get(n["k"])
    out_cols = []
    for ci, c in enumerate(cols):
        absn = [abs(v) for v in c["net"]]
        kmax = max(absn) if absn else 0
        rows = []
        for k, v in zip(c["k"], c["net"]):
            if abs(k - spot) <= GRID_STRIKES * 50 + 25:
                r = {"k": k, "net": round(v, 2), "pct": round(abs(v) / kmax * 100, 1) if kmax else 0.0}
                if ci == 0:
                    r["v5"], r["v15"] = v5.get(k), v15.get(k)
                rows.append(r)
        out_cols.append({"expiry": c["expiry"], "days": c["days"], "flip": c["flip"], "total_cr": c["total_cr"], "straddle": c["straddle"],
                         "king": c["k"][absn.index(kmax)] if kmax else None, "strikes": rows})
    return {"asof": now.isoformat(timespec="seconds"), "symbol": "NIFTY", "spot": round(spot, 2), "front": front["expiry"],
            "exp_move": exp_move, "expected": expected_move(spot, prev_close, front.get("atm_iv")), "regime": regime(front, spot), "columns": out_cols, "nodes": cl["nodes"], "structure": cl["structure"]}
