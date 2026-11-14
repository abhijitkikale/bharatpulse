import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import eod_nodes  # noqa: E402

KS = [24800, 24850, 24900, 24950, 25000, 25050, 25100, 25150, 25200, 25250, 25300]


def _gx(front, second=None, iv=15.0):
    rows = [front] + ([second] if second else [])
    metas = [{"e": "2026-11-17", "dte": 4, "net_cr": 120.0, "flip": 25050.0, "call_wall": 25200, "put_wall": 25000}]
    if second:
        metas.append({"e": "2026-11-24", "dte": 11, "net_cr": 60.0, "flip": 25040.0, "call_wall": 25300, "put_wall": 24900})
    return {"atm_iv": {"2026-11-17": iv, "2026-11-24": iv - 1}, "surface": {"k": KS, "expiries": metas, "net": rows, "total": rows}}


FRONT = [2, 3, 4, 5, -80, 5, 4, 3, 100, 6, 40]            # King +100 at 25200, negative 25000 (80%), +40 at 25300 (40%)
SECOND = [None, 1, 1, 2, -30, 2, 1, 1, 50, 2, 10]


def test_block_roles_grid_and_expected_move():
    b = eod_nodes.block(_gx(FRONT, SECOND), 25100.0)
    f = b["front"]
    assert f["expiry"] == "2026-11-17" and f["structure"]["king"] == 25200 and f["structure"]["floor"] == 25000 and f["structure"]["ceiling"] == 25200
    assert [n["k"] for n in f["nodes"]][:3] == [25200, 25000, 25300] and f["nodes"][0]["pct"] == 100.0 and f["nodes"][1]["sign"] == "-"
    g = b["grid"]
    assert g["k"] == KS and len(g["cols"]) == 2 and g["cols"][0]["king"] == 25200 and g["cols"][1]["king"] == 25200 and g["cols"][1]["net"][0] == 0.0
    assert g["cols"][0]["pct"][KS.index(25200)] == 100.0 and g["cols"][0]["pct"][KS.index(25000)] == 80.0
    e = b["expected"]
    sig = 25100.0 * 0.15 * (1 / 365) ** 0.5
    assert abs(e["sigma"] - round(sig, 1)) < 1e-9 and e["basis"] == "close" and "pace" not in e and e["up1"] > 25100 > e["dn1"]
    json.dumps(b, allow_nan=False)                            # the page embeds it as JSON


def test_block_without_a_surface_or_without_volatility():
    assert eod_nodes.block(None, 25000.0) is None and eod_nodes.block({}, 25000.0) is None and eod_nodes.block({"surface": None}, 25000.0) is None
    gx = _gx(FRONT)
    gx["atm_iv"] = {}
    assert eod_nodes.block(gx, 25100.0)["expected"] is None


def test_the_options_block_carries_nodes_and_the_template_renders_them():
    tpl = (pathlib.Path(__file__).resolve().parent.parent / "pipeline" / "template.html").read_text(encoding="utf-8")
    assert "renderNodesCard" in tpl and "op-nodes-card" in tpl and "Tomorrow's nodes" in tpl
    jr = (pathlib.Path(__file__).resolve().parent.parent / "pipeline" / "journal_dashboard.py").read_text(encoding="utf-8")
    assert "Rules scoreboard" in jr and "scoreHtml" in jr
