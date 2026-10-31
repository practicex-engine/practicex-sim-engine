import sys, statistics as S, itertools, json
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import state as st, run as r, clock
from engine.sync import FIXED_ENGINE_PARAMS
from plan_model import purchase
BASE = dict(FIXED_ENGINE_PARAMS, purchase_conversion=0.0133, organic_visitors_per_day=30, external_visitors_per_day=50, ad_budget_monthly=3_000_000, ad_cpc=700)

def sim(seed, nss, nfw, params=BASE):
    pur = []
    if nss: pur.append(purchase("SS27", "2027-01-31", "SS", nss))
    if nfw: pur.append(purchase("FW27", "2027-07-31", "FW", nfw))
    s = st.new_state(seed, "2026-11-01T00:00:00+09:00"); p = dict(params, purchases=pur)
    for i in range(clock.TOTAL_DAYS): r.process_day(s, p, i)
    L = s["ledger"]; t = L["tot"]
    return dict(neg=L["cash_neg_days"] > 0, min_cash=L["min_cash"], equity=L["cash"] + L["inv_value"], cash=L["cash"], stock=L["stock"],
                filled=t["orders"], lost=t["lost"], revenue=t["revenue"])

def agg(nss, nfw, seeds=range(1, 201), params=BASE):
    rs = [sim(s, nss, nfw, params) for s in seeds]
    first_stock_lost = None
    return dict(nss=nss, nfw=nfw, p_neg=sum(x["neg"] for x in rs) / len(rs), equity=S.mean(x["equity"] for x in rs) / 1e6,
                eq_p10=sorted(x["equity"] for x in rs)[len(rs)//10] / 1e6, cash=S.mean(x["cash"] for x in rs) / 1e6,
                min_cash=S.mean(x["min_cash"] for x in rs) / 1e6, filled=S.mean(x["filled"] for x in rs), lost=S.mean(x["lost"] for x in rs),
                stock=S.mean(x["stock"] for x in rs), revenue=S.mean(x["revenue"] for x in rs) / 1e6)
if __name__ == "__main__":
    grid = [0, 3, 5, 8, 10, 15, 20, 30, 50, 100]
    rows = []
    for a in grid:
        for b in grid:
            rows.append(agg(a, b, seeds=range(1, 61)))
    json.dump(rows, open("grid.json", "w"))
    print("nss nfw p_neg equity(M) eq_p10 cash(M) mincash filled lost stock")
    for x in sorted(rows, key=lambda x: -x["equity"])[:25]:
        print(x["nss"], x["nfw"], round(x["p_neg"], 2), round(x["equity"]), round(x["eq_p10"]), round(x["cash"]), round(x["min_cash"]), round(x["filled"]), round(x["lost"]), round(x["stock"]))
