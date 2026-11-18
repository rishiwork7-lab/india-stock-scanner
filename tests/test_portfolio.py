import copy

from src import portfolio as pf


def stock(code, mom_1y, industry, signal="Buy", tv=50.0, atr=3.0, price=100.0, results_in=None, ret_1m=0.0):
    nr = {"date": "2026-11-20", "sessions": results_in, "confirmed": True} if results_in is not None else None
    return {"code": code, "name": code.title(), "industry": industry, "price": price, "ret_1y": mom_1y, "ret_1m": ret_1m,
            "traded_value_cr": tv, "swing": {"signal": signal, "atr_pct": atr, "setup": "pullback", "stop": price * 0.92},
            "events": {"next_results": nr}}


def scan(date="2026-11-13", **px):
    stocks = [stock("AAA", 80, "Bank"), stock("BBB", 70, "Bank"), stock("CCC", 60, "IT"), stock("DDD", 50, "Auto"),
              stock("EEE", 90, "Pharma", signal="Avoid"), stock("FFF", 95, "Metal", tv=2), stock("GGG", 99, "Power", atr=7),
              stock("HHH", 98, "Media", results_in=3), stock("III", 40, "FMCG")]
    for s in stocks:
        s["price"] = px.get(s["code"], s["price"])
    etfs = [{"code": "NIFTYBEES", "name": "Nifty ETF", "price": px.get("NIFTYBEES", 250.0), "tracks": "Nifty 50"},
            {"code": "GOLDBEES", "name": "Gold ETF", "price": px.get("GOLDBEES", 100.0), "tracks": "Gold"}]
    return {"date": date, "stocks": stocks, "etfs": etfs}


def test_candidates_apply_every_check_and_one_per_industry():
    c = [x["code"] for x in pf.candidates(scan())]
    assert c == ["AAA", "CCC", "DDD", "III"]     # BBB: same industry as AAA; EEE Avoid; FFF thin; GGG volatile; HHH results in 3 sessions
    assert pf.momentum_12_1({"ret_1y": 21.0, "ret_1m": 10.0}) == 10.0


def test_plan_five_slots_whole_shares_and_weights():
    p = pf.plan(scan())
    assert [h["code"] for h in p["holdings"]] == ["NIFTYBEES", "GOLDBEES", "AAA", "CCC", "DDD"]
    assert round(sum(h["weight_pct"] for h in p["holdings"]), 0) == 100
    assert p["holdings"][0]["qty"] == 160 and p["holdings"][2]["qty"] == 166          # 40,000/250 ; 16,666/100
    assert abs(p["invested"] + p["cash"] - 100_000) < 0.01 and p["cash"] >= 0


def test_unfilled_stock_slots_stay_in_cash():
    s = scan()
    for x in s["stocks"]:
        x["swing"]["signal"] = "Avoid"
    p = pf.plan(s)
    assert len(p["holdings"]) == 2 and p["unfilled_pct"] == 50.0 and p["cash"] > 49_000


def test_paper_record_starts_marks_and_rebalances_monthly():
    st = pf.step(None, scan())
    assert st["start"] == "2026-11-13" and set(st["holdings"]) == {"NIFTYBEES", "GOLDBEES", "AAA", "CCC", "DDD"}
    assert st["cash"] > 0 and st["fees"] > 0 and st["history"][0]["value"] < 100_000          # charges paid
    again = pf.step(st, scan())
    assert len(again["history"]) == 1 and len(again["trades"]) == len(st["trades"])        # same date: no new trades
    up = pf.step(st, scan(date="2026-11-16", AAA=110.0, NIFTYBEES=255.0))
    assert len(up["history"]) == 2 and len(up["trades"]) == len(st["trades"]) and up["history"][1]["value"] > st["history"][0]["value"]
    assert up["history"][1]["bench"] > st["history"][0]["bench"]
    # new month: AAA turns Avoid, so it is sold and BBB (next best, its industry now free) is bought; CCC and DDD are kept
    s = scan(date="2026-12-01")
    next(x for x in s["stocks"] if x["code"] == "AAA")["swing"]["signal"] = "Avoid"
    dec = pf.step(up, s)
    assert set(dec["holdings"]) == {"NIFTYBEES", "GOLDBEES", "BBB", "CCC", "DDD"} and dec["last_rebalance"] == "2026-12"
    sold = [t for t in dec["trades"] if t["date"] == "2026-12-01" and t["side"] == "Sell"]
    assert [t["code"] for t in sold][:1] == ["AAA"]
    r = pf.report(dec, s)
    assert r["sessions"] == 3 and r["start"] == "2026-11-13" and abs(sum(h["weight_pct"] for h in r["holdings"]) + r["cash"] / r["value"] * 100 - 100) < 0.5


def test_held_stock_kept_while_in_top_ten():
    st = pf.step(None, scan())
    s = scan(date="2026-12-01")
    s["stocks"].append(stock("NEW", 200, "Chemicals"))                       # a stronger stock appears
    dec = pf.step(copy.deepcopy(st), s)
    assert {"AAA", "CCC", "DDD"} <= set(dec["holdings"]) and "NEW" not in dec["holdings"]


# ---- challengers and the promotion rule --------------------------------------------------------------------------------
def with_sectors(s):
    s["etfs"] += [{"code": c, "name": c, "price": 50.0, "tracks": c} for c in ("BANKX", "ITX", "PHX", "AUTX")]
    s["sectors"] = [{"code": "BANKX", "rank": 1, "momentum": 9.0, "above_ma200": True}, {"code": "ITX", "rank": 2, "momentum": 7.0, "above_ma200": False},
                    {"code": "PHX", "rank": 3, "momentum": 5.0, "above_ma200": True}, {"code": "AUTX", "rank": 4, "momentum": 3.0, "above_ma200": True}]
    return s


def test_each_challenger_changes_one_thing():
    s = with_sectors(scan())
    w = lambda sp: {t["code"]: round(t["weight"], 4) for t in pf.targets(s, spec=pf.BY_ID[sp])}  # noqa: E731
    assert w("C1") == {"NIFTYBEES": 0.5, "AAA": 0.1667, "CCC": 0.1667, "DDD": 0.1667}
    assert w("C2") == {"NIFTYBEES": 0.4, "GOLDBEES": 0.1, "BANKX": 0.1667, "PHX": 0.1667, "AUTX": 0.1667}   # ITX below its 200-day average
    for x in s["stocks"]:
        x["swing"]["atr_pct"] = {"AAA": 4.5, "CCC": 2.0, "DDD": 3.0, "III": 1.0}.get(x["code"], x["swing"]["atr_pct"])
    assert list(w("C3"))[2:] == ["III", "CCC", "BBB"]          # calmest first; BBB ties DDD on range, wins on strength, takes the bank slot
    c4 = w("C4")
    assert sum(1 for k in c4 if k not in ("NIFTYBEES", "GOLDBEES")) == 4 and abs(c4["AAA"] - 0.0833) < 1e-3   # only 4 pass; 2 slots in cash


def record(values, start="2025-01-01", step_days=30):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return {"history": [{"date": (d0 + dt.timedelta(days=i * step_days)).isoformat(), "value": v, "bench": v} for i, v in enumerate(values)]}


def test_promotion_needs_a_year_a_clear_gain_both_halves_and_no_deeper_fall():
    champ = record([100 + i for i in range(14)])                        # ~13 months, +13%
    better = record([100 + 2 * i for i in range(14)])                   # +26%, ahead in both halves
    states = {"P1": champ, "C1": better}
    sw = pf.promotion_check(states, "P1", "2026-02-01")
    assert sw["to"] == "C1" and sw["gain_pts"] >= 2
    assert pf.promotion_check({"P1": record([100 + i for i in range(6)]), "C1": record([100 + 3 * i for i in range(6)])}, "P1", "x") is None  # too short
    lucky = record([100] * 7 + [100 + 4 * i for i in range(1, 8)])     # all the gain in the second half
    assert pf.promotion_check({"P1": champ, "C1": lucky}, "P1", "x") is None
    crashy = record([100 + 2 * i for i in range(7)] + [80] + [100 + 3 * i for i in range(7, 13)])
    assert pf.promotion_check({"P1": champ, "C1": crashy}, "P1", "x") is None           # fell 20% vs 0%


def test_build_plans_restores_old_and_new_formats(tmp_path):
    from src.site_builder import build_plans
    s = with_sectors(scan())
    first = build_plans(s, tmp_path / "a", lambda: None)
    assert first["champion"] == "P1" and set(first["plans"]) == {sp.id for sp in pf.SPECS}
    s2 = with_sectors(scan(date="2026-11-16"))
    old = build_plans(s2, tmp_path / "b", lambda: {"state": first["plans"]["P1"]["state"]})          # format before challengers
    assert old["plans"]["P1"]["record"]["start"] == "2026-11-13" and old["plans"]["C1"]["record"]["start"] == "2026-11-16"
    new = build_plans(s2, tmp_path / "c", lambda: first)
    assert all(v["record"]["start"] == "2026-11-13" for v in new["plans"].values()) and new["plans"]["C2"]["record"]["sessions"] == 2
    again = build_plans(s2, tmp_path / "c", lambda: 1 / 0)                                            # saved files win, no fetch
    assert again["plans"]["P1"]["record"]["sessions"] == 2
