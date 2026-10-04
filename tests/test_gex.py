import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import numpy as np
import pandas as pd

from gex import black76, compute_gex, forward_from_parity, gamma76, implied_vol

R = 0.065


def test_implied_vol_round_trip():
    F, T, sig = 22000.0, 30 / 365, 0.18
    for K in (20000.0, 21500.0, 22000.0, 22500.0, 24000.0):
        for is_call in (True, False):
            px = float(black76(F, K, T, sig, R, is_call))
            if px < 0.5:
                continue
            iv = float(implied_vol(np.array([px]), F, np.array([K]), T, R, np.array([is_call]))[0])
            assert abs(iv - sig) < 1e-4, (K, is_call, iv)


def test_gamma_matches_finite_difference():
    F, K, T, sig, h = 22000.0, 22100.0, 20 / 365, 0.17, 1.0
    delta = lambda f: (float(black76(f + h, K, T, sig, R, True)) - float(black76(f - h, K, T, sig, R, True))) / (2 * h)
    numeric = (delta(F + h) - delta(F - h)) / (2 * h)
    assert abs(float(gamma76(F, K, T, sig, R)) - numeric) / numeric < 1e-3


def test_forward_from_parity():
    F, T = 22150.0, 10 / 365
    ks = np.arange(21800, 22600, 50.0)
    c = black76(F, ks, T, 0.15, R, True)
    p = black76(F, ks, T, 0.15, R, False)
    assert abs(forward_from_parity(ks, c, p, T, R, 22100.0) - F) < 1.0


def _chain(spot, call_oi_fn, put_oi_fn, sigma=0.16, days=7, tdate="2026-10-01"):
    T = days / 365
    F = spot * math.exp(R * T)
    exp = (pd.Timestamp(tdate) + pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []
    for k in np.arange(spot * 0.85, spot * 1.15, 50.0):
        for typ, oi_fn, is_call in (("CE", call_oi_fn, True), ("PE", put_oi_fn, False)):
            px = float(black76(F, k, T, sigma, R, is_call))
            rows.append({"XpryDt": exp, "StrkPric": float(k), "OptnTp": typ, "ClsPric": round(px, 2), "SttlmPric": round(px, 2),
                         "OpnIntrst": oi_fn(k), "TtlTradgVol": 100})
    return pd.DataFrame(rows), tdate


def test_model_recovers_vol_and_signs():
    spot = 22000.0
    df, d = _chain(spot, lambda k: 100000 if k > spot else 20000, lambda k: 20000 if k > spot else 100000)
    out = compute_gex(df, d, spot)
    assert out is not None
    iv = list(out["atm_iv"].values())[0]
    assert abs(iv - 16.0) < 1.0                      # recovers the 16% used to price the chain
    near = out["nearest"]
    assert any(v > 0 for v in near["call"]) and any(v < 0 for v in near["put"])      # calls positive, puts negative
    assert near["walls"]["call_gex"] >= spot and near["walls"]["put_gex"] < spot     # walls on the correct sides


def test_gamma_flip_found_between_put_and_call_mass():
    spot = 22000.0
    # call OI concentrated above spot, put OI concentrated below spot, more puts -> net negative near spot, flip above/below
    df, d = _chain(spot, lambda k: 60000 if k > spot else 5000, lambda k: 90000 if k <= spot else 5000)
    out = compute_gex(df, d, spot)["nearest"]
    assert out["total_cr"] < 0                                   # put gamma dominates at spot
    assert out["flip"] is not None and out["flip"] > spot        # positive-gamma zone only at higher levels


def test_returns_none_without_priced_options():
    df, d = _chain(22000.0, lambda k: 1000, lambda k: 1000)
    df["ClsPric"] = 0.0
    df["SttlmPric"] = 0.0
    assert compute_gex(df, d, 22000.0) is None
