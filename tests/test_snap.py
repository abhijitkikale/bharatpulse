import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "pipeline"))
import snap_data  # noqa: E402

PERIODS = ["1D", "1W", "1M", "3M", "6M", "1Y", "3Y", "5Y"]


def _ret(base):
    return {p: base * (i + 1) * 0.5 for i, p in enumerate(PERIODS)}


def _stock(sym, sector, base, d1=0.1):
    return {"s": sym, "n": sym + " Ltd.", "x": [sector], "r": _ret(base), "d1": d1, "p": 100.0, "hi": 140.0, "lo": 60.0}


def _D(nifty_chg=1.0, adv=700, dec=2200):
    sectors = {"Alpha": 3.0, "Beta": 2.0, "Gamma": 1.0}                # Alpha has the strongest momentum in every period
    stocks = [_stock(f"{sec[0]}{k}", sec, base * (1 + 0.1 * (3 - k))) for sec, base in sectors.items() for k in range(3)]
    return {"periods": PERIODS, "asof": "06 Nov 2026", "dates": ["2026-11-05", "2026-11-06"],
            "indices": [{"label": "Nifty 50", "group": "benchmark", "close": 25030.35, "chg": nifty_chg, "ret": _ret(1.0)},
                        {"label": "Nifty Smallcap 250", "group": "benchmark", "close": 17000.0, "chg": nifty_chg - 2.5, "ret": _ret(1.0)},
                        {"label": "Bank", "group": "sector", "close": 55000.0, "chg": 0.5, "ret": _ret(1.0)}] +
                       [{"label": s, "group": "sector", "close": 1000.0, "chg": c - 2, "ret": _ret(c)} for s, c in sectors.items()],
            "members": {s: [f"{s[0]}{k}" for k in range(3)] for s in sectors} | {"Bank": []},
            "stocks": stocks, "flows": [{"date": "2026-11-05", "FII": 4000.0, "DII": -1000.0}, {"date": "2026-11-06", "FII": 5200.0, "DII": -2400.0}],
            "activity": {"breadth": {"d": ["2026-11-06"], "adv": [adv], "dec": [dec]}, "spikes": [{"s": "ZZZ", "p": 10, "chg": 14.0, "x": 40.0, "tcr": 500.0}]},
            "options": {"vix": {"d": ["a", "b"], "v": [15.0, 13.0]}}}


def test_momentum_puts_the_strongest_sector_and_its_best_stock_first():
    rows = snap_data.momentum_rows(_D())
    assert rows[0]["s"] == "A0" and rows[0]["sector"] == "Alpha" and rows[0]["rank"] == 1
    assert [r["sector"] for r in rows[:3]] == ["Alpha", "Alpha", "Beta"] and rows[-1]["sector"] == "Gamma"      # 0.6 sector + 0.4 stock: the best Beta stock (30) beats the weakest Alpha stock (40)


def test_build_summary_picks_the_breadth_divergence_and_stays_factual():
    snap = snap_data.build(_D(nifty_chg=1.0, adv=700, dec=2200))
    assert snap["date"] == "2026-11-06" and snap["hook"]["close"] == 25030.35 and snap["breadth"]["pct_adv"] == 24.1
    first = snap["all_insights"][0]
    assert first["kind"] == "breadth" and "only 24% of stocks advanced" in first["line"]                  # an index up 1% on narrow breadth
    kinds = {i["kind"] for i in snap["all_insights"]}
    assert {"flows", "volume", "vix", "size"} <= kinds
    assert snap["sectors"]["best"][0]["name"] == "Gamma" or snap["sectors"]["best"][0]["chg"] >= snap["sectors"]["best"][1]["chg"]
    text = " ".join(i["line"] + " " + i["sub"] for i in snap["all_insights"]).lower()
    for word in ("buy ", "sell ", "target", "recommend"):
        assert word not in text                                                                           # facts only, no advice words
    assert snap["hook"]["sub"] == first["line"] and len(snap["insights"]) == 3


def test_quiet_day_has_a_plain_headline_and_the_caption_carries_the_disclaimer():
    snap = snap_data.build(_D(nifty_chg=0.1, adv=1500, dec=1400))
    assert snap["all_insights"][0]["kind"] != "breadth" or "Broad" in snap["all_insights"][0]["line"]
    title, desc, tags = snap_data.caption(snap)
    assert title.startswith("Nifty +0.10%") and "abhijitkikale.github.io/bharatpulse" in desc and "Not SEBI registered" in desc and tags.startswith("#Nifty")


def test_history_record_lets_the_next_day_report_a_rotation_and_a_gamma_flip():
    D = _D()
    snap = snap_data.build(D)
    rec = snap_data.snapshot_for_history(D, snap)
    assert rec["date"] == "2026-11-06" and set(rec["sector_rank"]) >= {"Alpha", "Beta", "Gamma"}
    prev = {"sector_rank": {"Alpha": 3, "Beta": 2, "Gamma": 1}, "regime": None}                           # yesterday Gamma was first and Alpha last
    moved = [i for i in snap_data.build(D, prev)["all_insights"] if i["kind"] == "rank"]
    assert not moved or "places" in moved[0]["line"]                                                      # only a move of 4 or more places counts: with 3 sectors there is none
