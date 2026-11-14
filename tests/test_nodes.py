import collections
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import gex  # noqa: E402
import nodes  # noqa: E402

IST = "Asia/Kolkata"


def _col(pairs, total=100.0, flip=25050.0):
    ks = [k for k, _ in pairs]
    net = [v for _, v in pairs]
    return {"expiry": "2026-11-17", "days": 4, "flip": flip, "total_cr": total, "k": ks, "net": net, "call": [max(v, 0) for v in net], "put": [min(v, 0) for v in net], "straddle": 200.0}


def _candles(rows, start="2026-11-13 09:15"):
    idx = pd.date_range(start, periods=len(rows), freq="min", tz=IST)
    return pd.DataFrame(rows, columns=["h", "l"], index=idx)


def test_roles_king_gatekeeper_floor_ceiling_air_pocket_and_midpoint():
    pairs = [(24900, 5), (24950, 8), (25000, -60), (25050, 4), (25100, 3), (25150, 4), (25200, 55), (25250, 6), (25300, 100), (25350, 5), (25400, 3), (25450, 2)]
    c = nodes.classify(_col(pairs), 25180.0)
    s = c["structure"]
    assert s["king"] == 25300 and s["gatekeeper"] == 25200 and s["floor"] == 25000 and s["ceiling"] == 25200
    by = {n["k"]: n for n in c["nodes"]}
    assert set(by) == {25000, 25200, 25300} and by[25300]["pct"] == 100.0 and by[25000]["sign"] == "-" and "king" in by[25300]["roles"]
    assert "gatekeeper" in by[25200]["roles"] and "ceiling" in by[25200]["roles"] and "floor" in by[25000]["roles"] and "barney" in by[25000]["roles"]
    assert [25050, 25150] in s["air_pockets"]                      # 25050, 25100, 25150 are all under 10% of the King
    assert s["midpoint"] is False and s["range_pos"] == 0.9         # spot 25180 is 90% of the way from floor 25000 to ceiling 25200
    c2 = nodes.classify(_col(pairs), 25100.0)
    assert c2["structure"]["midpoint"] is True and c2["structure"]["range_pos"] == 0.5


def test_decoy_flag_and_touches_and_fresh():
    pairs = [(25000, 40), (25100, 5), (25200, 5), (25600, 100)]
    c = nodes.classify(_col(pairs), 25100.0, candles=_candles([[25110, 25090], [25105, 25001], [25120, 25040], [25030, 24990]]), exp_move=100.0)
    by = {n["k"]: n for n in c["nodes"]}
    assert "decoy" in by[25600]["flags"] and by[25600]["fresh"] is True
    assert by[25000]["touches"] == 2 and by[25000]["fresh"] is False          # entered the 25000 zone twice (candle 2, then again in candle 4)
    assert nodes.touches(None, 25000) == 0


def test_velocity_building_and_melting_from_the_buffer():
    now = pd.Timestamp("2026-11-13 11:00", tz=IST)
    hist = collections.deque([(now - pd.Timedelta(minutes=6), {25000: 20.0, 25100: 50.0, 25200: 100.0}), (now - pd.Timedelta(minutes=1), {25000: 25.0, 25100: 45.0, 25200: 100.0})])
    v = nodes.velocity(hist, now, [25000, 25100, 25200], [30.0, 40.0, 100.0], 5)
    assert v == {25000: 10.0, 25100: -10.0, 25200: 0.0}                      # versus the reading 6 minutes ago (the newest one at least 5 minutes old), in points of the King
    assert nodes.velocity(hist, now, [25000], [30.0], 15) == {}              # buffer does not reach back 15 minutes


def test_regime():
    assert nodes.regime(_col([(1, 1)], total=5.0, flip=25000.0), 25100.0) == {"now": "POSITIVE", "side": 1, "flip": 25000.0}
    assert nodes.regime(_col([(1, 1)], total=-5.0, flip=25200.0), 25100.0) == {"now": "NEGATIVE", "side": -1, "flip": 25200.0}
    assert nodes.regime(_col([(1, 1)], total=-5.0, flip=None), 25100.0)["side"] == 0


def _snap(nds, regime=None):
    return {"nodes": nds, "regime": regime or {"now": "POSITIVE", "side": 1, "flip": 25000.0}}


def _n(k, pct, roles=()):
    return {"k": k, "pct": pct, "roles": list(roles), "net": pct, "sign": "+", "touches": 0, "fresh": True, "flags": []}


def test_grade_a_for_a_backed_roomy_fresh_setup_and_c_for_a_blocked_one():
    now = pd.Timestamp("2026-11-13 11:10", tz=IST)
    levels = [{"name": "Now VWAP", "value": 25100.0}, {"name": "POC", "value": 25104.0}, {"name": "VAL", "value": 25095.0}]
    snap = _snap([_n(25100, 70, ["floor"]), _n(25200, 100, ["king"])])
    g = nodes.grade_setup({"when": "11:05", "side": "CALL", "level": "Now VWAP 25100.00 (bounce)"}, snap, levels, _candles([[25300, 25200]] * 5, "2026-11-13 09:15"), "2026-11-13", now)
    assert g["grade"] == "A" and g["score"] == 5 and g["late"] is True and any("backed" in r for r in g["reasons"])        # backing +2, room 100 points +1, fresh +1, confluence +1
    blocked = _snap([_n(25100, 30), _n(25110, 60, ["gatekeeper"])])
    g2 = nodes.grade_setup({"when": "11:08", "side": "CALL", "level": "Now VWAP 25100.00 (bounce)"}, blocked, [], None, "2026-11-13", now)
    assert g2["grade"] in ("B", "C") and any("blocked" in r for r in g2["reasons"])
    empty = _snap([_n(25500, 100)])
    g3 = nodes.grade_setup({"when": "11:08", "side": "PUT", "level": "Now VWAP 25100.00 (bounce)"}, empty, [], None, "2026-11-13", now)
    assert g3["grade"] == "C" and g3["score"] == 0 and any("no significant node within" in r for r in g3["reasons"])      # room +1 (nothing ahead), nothing nearby -1
    assert nodes.grade_setup({"when": "11:08", "side": "PUT", "level": "no number"}, empty, [], None, "d", now) is None


def test_negative_gamma_against_the_move_costs_two_points_and_setup_key_ignores_drifting_numbers():
    now = pd.Timestamp("2026-11-13 11:10", tz=IST)
    neg = _snap([_n(25100, 70), _n(25200, 100)], {"now": "NEGATIVE", "side": -1, "flip": 25200.0})
    call = nodes.grade_setup({"when": "11:09", "side": "CALL", "level": "Now VWAP 25100.00 (bounce)"}, neg, [], None, "2026-11-13", now)
    put = nodes.grade_setup({"when": "11:09", "side": "PUT", "level": "Now VWAP 25100.00 (bounce)"}, neg, [], None, "2026-11-13", now)
    assert any("against the move -2" in r for r in call["reasons"]) and any("with the move +1" in r for r in put["reasons"])
    assert nodes.setup_key("d", "11:12", "CALL", "Now VWAP 25172.77 (bounce)") == nodes.setup_key("d", "11:12", "CALL", "Now VWAP 25172.65 (bounce)")


def _chain(spot=25100.0, expiries=("2026-11-17", "2026-11-24", "2026-12-01")):
    rows = []
    for e in expiries:
        T = max((pd.Timestamp(e) - pd.Timestamp("2026-11-13")).days, 1) / 365.0
        F = spot * math.exp(0.065 * T)
        for k in range(24000, 26250, 50):
            for typ in ("CE", "PE"):
                px = float(gex.black76(F, float(k), T, 0.15, 0.065, typ == "CE"))
                oi = 100000.0
                if typ == "CE" and k == 25300:
                    oi = 2000000.0
                if typ == "PE" and k == 25000:
                    oi = 1500000.0
                rows.append({"XpryDt": e, "StrkPric": float(k), "OptnTp": typ, "ltp": round(max(px, 0.05), 2), "oi": oi, "prev_oi": oi, "volume": 1000.0, "symbol": f"NIFTY{e}{k}{typ}"})
    return {"spot": spot, "rows": rows}


def test_columns_and_snapshot_from_a_synthetic_chain():
    now = pd.Timestamp("2026-11-13 11:10", tz=IST)
    cols = nodes.columns(_chain(), now)
    assert [c["expiry"] for c in cols] == ["2026-11-17", "2026-11-24", "2026-12-01"] and all(c["k"] and len(c["k"]) == len(c["net"]) for c in cols)
    assert cols[0]["straddle"] and cols[0]["straddle"] > 0
    snap = nodes.build_snapshot(_chain(), now, _candles([[25110, 25090]] * 3))
    assert snap["front"] == "2026-11-17" and snap["structure"]["king"] in (25300, 25000) and snap["nodes"][0]["pct"] == 100.0
    assert len(snap["columns"]) == 3 and all(abs(r["k"] - 25100) <= 1025 for r in snap["columns"][0]["strikes"]) and snap["regime"]["now"] in ("POSITIVE", "NEGATIVE")


def test_expected_move_levels_and_pace():
    e = nodes.expected_move(25200.0, 25000.0, 14.0)
    sig = 25000.0 * 0.14 * (1 / 365) ** 0.5
    assert abs(e["sigma"] - round(sig, 1)) < 1e-9 and e["up1"] == round(25000 + sig, 1) and e["dn2"] == round(25000 - 2 * sig, 1)
    assert abs(e["pace"] - round(200 / sig, 2)) < 1e-9 and e["basis"] == "previous close"
    assert nodes.expected_move(25000.0, None, 14.0) is None and nodes.expected_move(25000.0, 25000.0, None) is None


def test_grade_flags_blocked_midpoint_contra_nonode():
    now = pd.Timestamp("2026-11-13 11:10", tz=IST)
    snap = _snap([_n(25100, 30), _n(25110, 60, ["gatekeeper"])])
    g = nodes.grade_setup({"when": "11:08", "side": "CALL", "level": "Now VWAP 25100.00 (bounce)"}, snap, [], None, "2026-11-13", now)
    assert "blocked" in g["flags"]
    snap = _snap([_n(25050, 70, ["floor"]), _n(25150, 80, ["ceiling"])])
    snap["structure"] = {"floor": 25050, "ceiling": 25150, "midpoint": True}
    g = nodes.grade_setup({"when": "11:08", "side": "CALL", "level": "Now VWAP 25100.00 (bounce)"}, snap, [], None, "2026-11-13", now)
    assert "midpoint" in g["flags"] and any("middle of the range 25050-25150" in r for r in g["reasons"])
    neg = _snap([_n(25100, 70)], {"now": "NEGATIVE", "side": -1, "flip": 25200.0})
    assert "contra" in nodes.grade_setup({"when": "11:09", "side": "CALL", "level": "Now VWAP 25100.00 (bounce)"}, neg, [], None, "2026-11-13", now)["flags"]
    assert "nonode" in nodes.grade_setup({"when": "11:09", "side": "PUT", "level": "Now VWAP 25100.00 (bounce)"}, _snap([_n(25500, 100)]), [], None, "2026-11-13", now)["flags"]
