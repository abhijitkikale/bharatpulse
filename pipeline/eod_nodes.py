"""Tomorrow's nodes for the published end-of-day dashboard (Options tab): the same node logic as the live agent (pipeline/nodes.py), built from the end-of-day
gamma surface that compute_gex already makes (strike x expiry, rupee crore per 1% move, calls positive and puts negative), plus the next day's priced move
(1 and 2 sigma around the close from the front expiry's at-the-money implied volatility). Pure function, no network; a failure leaves the tab as it was."""
import numpy as np

import nodes


def block(gx, spot, n_cols=6, half_window=12):
    """gx: compute_gex() output; spot: the close. -> {"front": {...}, "grid": {...}, "expected": {...} or None} or None."""
    sf = (gx or {}).get("surface")
    if not sf or not sf.get("expiries") or not sf.get("k"):
        return None
    ks = [float(k) for k in sf["k"]]
    metas, net = sf["expiries"], sf["net"]
    cols = [(m, [0.0 if v is None else float(v) for v in row]) for m, row in zip(metas, net)]
    if not cols:
        return None
    front_meta, front_net = cols[0]
    cl = nodes.classify({"k": ks, "net": front_net}, float(spot))
    step = float(np.median(np.diff(ks))) if len(ks) > 1 else 50.0
    win = [i for i, k in enumerate(ks) if abs(k - spot) <= half_window * step]
    grid_cols = []
    for m, row in cols[:n_cols]:
        mx = max((abs(v) for v in row), default=0.0)
        king = ks[int(np.argmax(np.abs(row)))] if mx > 0 else None
        grid_cols.append({"e": m["e"], "dte": m["dte"], "flip": m.get("flip"), "net_cr": m.get("net_cr"), "king": None if king is None else int(king),
                          "net": [round(row[i], 1) for i in win], "pct": [round(abs(row[i]) / mx * 100, 1) if mx else 0.0 for i in win]})
    iv = (gx.get("atm_iv") or {}).get(front_meta["e"])
    exp = nodes.expected_move(spot, spot, iv)
    if exp:
        exp.pop("pace", None)
        exp["basis"] = "close"
    nds = [{"k": int(n["k"]), "net": n["net"], "pct": n["pct"], "sign": n["sign"], "roles": n["roles"], "flags": n["flags"]} for n in cl["nodes"]]
    st = cl["structure"]
    return {"front": {"expiry": front_meta["e"], "dte": front_meta["dte"], "flip": front_meta.get("flip"), "net_cr": front_meta.get("net_cr"), "nodes": nds,
                      "structure": {k: (None if v is None else (int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and k in ("king", "floor", "ceiling", "gatekeeper") else v))
                                    for k, v in st.items()}},
            "grid": {"k": [int(ks[i]) for i in win], "cols": grid_cols}, "expected": exp}
