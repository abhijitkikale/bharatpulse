import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd

from fetch_fyers import FO_COLS, candles_frame, provisional_fo


def _fo():
    rows = []
    for x, k, t, oi in (("2026-10-27", 22500, "CE", 100), ("2026-11-03", 22500, "CE", 1000), ("2026-11-03", 22500, "PE", 2000),
                        ("2026-11-03", 20000, "PE", 500), ("2026-11-03", 24000, "CE", 300)):
        rows.append(["2026-10-26", "IDO", "NIFTY", x, float(k), t, 50.0, 50.0, float(oi), 10.0, 999.0, 22750.0])
    return pd.DataFrame(rows, columns=FO_COLS)


def _chain(prev_scale=1.0):
    r = lambda x, k, t, oi, prev: {"XpryDt": x, "StrkPric": float(k), "OptnTp": t, "ltp": 60.0, "oi": oi, "prev_oi": prev * prev_scale, "volume": 77.0}
    return {"NIFTY": {"spot": 22749.75, "rows": [r("2026-11-03", 22500, "CE", 1100, 1000), r("2026-11-03", 22500, "PE", 2300, 2000)]}}


def test_provisional_day_uses_fyers_strikes_and_carries_the_rest():
    out, added = provisional_fo(_fo(), "2026-10-27", _chain())
    assert added == ["NIFTY"]
    new = out[out["TradDt"] == "2026-10-27"]
    assert set(new["XpryDt"]) == {"2026-10-27", "2026-11-03"} and new["Prov"].all()   # the contract expiring today is carried, like NSE's own file
    fy = new[(new["StrkPric"] == 22500) & (new["OptnTp"] == "PE")].iloc[0]
    assert fy["OpnIntrst"] == 2300 and fy["ChngInOpnIntrst"] == 300 and fy["ClsPric"] == 60 and fy["TtlTradgVol"] == 77
    far = new[(new["StrkPric"] == 20000)].iloc[0]                                 # not in Fyers' range: previous OI, no price
    assert far["OpnIntrst"] == 500 and far["ClsPric"] == 0 and far["ChngInOpnIntrst"] == 0
    assert (new["UndrlygPric"] == 22749.75).all()
    assert len(out[out["TradDt"] == "2026-10-26"]) == 5                           # history untouched


def test_chain_whose_previous_oi_does_not_match_nse_is_rejected():
    out, added = provisional_fo(_fo(), "2026-10-27", _chain(prev_scale=2))
    assert added == [] and (out["TradDt"] == "2026-10-27").sum() == 0


def test_big_jump_in_open_interest_is_fine_when_previous_day_matches():
    ch = _chain()
    ch["NIFTY"]["rows"][0]["oi"] = 5000                    # rollover on expiry day: OI can jump a lot
    out, added = provisional_fo(_fo(), "2026-10-27", ch)
    assert added == ["NIFTY"]


def test_repeated_fyers_candles_are_counted_once():
    c = [[1793000100, 10, 12, 9, 11, 500], [1793000040, 9, 11, 8, 10, 400], [1793000100, 10, 12, 9, 11, 500], [1793000040, 9, 11, 8, 10, 400]]
    d = candles_frame(c)
    assert len(d) == 2 and d["v"].sum() == 900 and list(d["o"]) == [9.0, 10.0]          # sorted by time, no double counting
    assert str(d.index.tz) == "Asia/Kolkata"
