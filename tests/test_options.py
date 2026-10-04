import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import pandas as pd

from compute_options import build_options, max_pain, participant_block
from fetch_fo import parse_participant, reduce_bhavcopy


def test_max_pain_hand_calc():
    # calls: 100->10, 110->10, 120->10 ; puts: 100->10, 110->10, 120->10  => symmetric, pain minimal at 110
    assert max_pain([100, 110, 120], [10, 10, 10], [10, 10, 10]) == 110
    # heavy put OI at 100, heavy call OI at 120: writers prefer expiry between -> any strike in between; at 100 puts pay 0, calls pay 0
    assert max_pain([100, 110, 120], [0, 0, 50], [50, 0, 0]) in (100, 110, 120)


def test_max_pain_pulls_toward_big_oi():
    # almost all OI at 110 on both sides -> expiry at 110 costs writers least
    assert max_pain([100, 110, 120], [1, 100, 1], [1, 100, 1]) == 110


def _row(day, exp, strike, typ, oi, chg=0, vol=0, und=100.0):
    return {"TradDt": day, "FinInstrmTp": "IDO", "TckrSymb": "NIFTY", "XpryDt": exp, "StrkPric": strike, "OptnTp": typ,
            "ClsPric": 1.0, "SttlmPric": 1.0, "OpnIntrst": oi, "ChngInOpnIntrst": chg, "TtlTradgVol": vol, "UndrlygPric": und}


def test_pcr_and_chain():
    rows = []
    for day in ("2026-09-30", "2026-10-01"):
        for k, ce, pe in ((95, 5, 20), (100, 10, 10), (105, 20, 5)):
            rows.append(_row(day, "2026-10-07", k, "CE", ce, vol=ce))
            rows.append(_row(day, "2026-10-07", k, "PE", pe, vol=pe))
    out = build_options(pd.DataFrame(rows), None, None)
    n = out["symbols"]["NIFTY"]
    assert n["hist"]["pcr_oi"][-1] == 1.0                      # 35 puts / 35 calls
    exp = n["chain"]["expiries"][0]
    assert exp["pain"] == 100 and exp["coi_total"] == 35 and exp["poi_total"] == 35
    assert n["chain"]["spot"] == 100.0 and len(exp["k"]) == 3


def test_reduce_keeps_only_index_derivatives():
    df = pd.DataFrame([
        _row("2026-10-01", "2026-10-07", 100, "CE", 1),
        {**_row("2026-10-01", "2026-10-27", 435, "PE", 5), "TckrSymb": "ABCAPITAL", "FinInstrmTp": "STO"},
    ])
    assert list(reduce_bhavcopy(df)["TckrSymb"]) == ["NIFTY"]


PART = ('"Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Oct 01, 2026",,,\n'
        "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short       ,Option Index Call Long,"
        "Option Index Put Long,Option Index Call Short,Option Index Put Short,Option Stock Call Long,Option Stock Put Long,"
        "Option Stock Call Short,Option Stock Put Short,Total Long Contracts      ,Total Short Contracts\n"
        "FII,29605,339779,3393649,2858568,670483,1114773,1101948,448195,112470,227850,206962,90579,5548830,5046031\n"
        "TOTAL,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n")


def test_participant_parse_and_net():
    rec = parse_participant(PART, "2026-10-01")
    assert len(rec) == 1 and rec[0]["who"] == "FII"
    blk = participant_block(pd.DataFrame(rec))
    assert blk["FII"]["fut_net"] == [29605 - 339779]
    assert blk["FII"]["call_net"] == [670483 - 1101948] and blk["FII"]["put_net"] == [1114773 - 448195]
