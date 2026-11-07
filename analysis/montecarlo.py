"""v1.0.0 현실화 오프라인 재계산: 시나리오 x 시드. 엔진 코드는 바꾸지 않고 상수/params만 덮어쓴다.
사용: python3 analysis/montecarlo.py [시드수] > out.json
"""
import json, os, sys, collections, statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import clock, run, state as st, sync, supply, ledger

R = json.load(open(os.path.join(os.path.dirname(__file__), "..", "plan", "realism.json"), encoding="utf-8"))
BASE = {"purchase_conversion": 0.033, "organic_visitors_per_day": 366, "external_visitors_per_day": 50,
        "ad_budget_monthly": 500000, "ad_cpc": 700, "external_fee_rate": 0.30}

SCEN = {
 "v0.9 기준(전환3.3%)":      {"conv": 0.033},
 "현실 기준(전환1.33%)":      {"conv": R["conversion"]["base"]},
 "비관(전환1.0%)":            {"conv": R["conversion"]["low"]},
 "낙관(전환2.0%)":            {"conv": R["conversion"]["high"]},
 "현실+반품20%":              {"conv": R["conversion"]["base"], "rr": R["return_rate"]["high"]},
 "현실+생산지연10일":          {"conv": R["conversion"]["base"], "delay": R["shocks"]["delay_days"]["v"]},
 "현실+불량3%":               {"conv": R["conversion"]["base"], "defect": R["shocks"]["defect_rate"]["v"]},
 "현실+복합충격":              {"conv": R["conversion"]["base"], "rr": R["return_rate"]["high"],
                              "delay": R["shocks"]["delay_days"]["v"], "defect": R["shocks"]["defect_rate"]["v"]},
}


def one(seed, sc):
    params = dict(BASE); params["purchase_conversion"] = sc["conv"]
    params.update(sync.FIXED_ENGINE_PARAMS)
    params["purchases"] = sync.load_purchases(); params["approvals"] = []
    params["catalog"] = sync.load_catalog(); params["ad_schedule"] = sync.load_ad_schedule(); params["exposure"] = sync.load_exposure()
    if "rr" in sc: params["return_rate"] = sc["rr"]
    od, ol = supply.DEFECT_RATE, ledger.PRODUCTION_DAYS
    supply.DEFECT_RATE = sc.get("defect", od); ledger.PRODUCTION_DAYS = ol + sc.get("delay", 0)
    try:
        s = st.new_state(seed, "2026-11-01T06:00:00+09:00")
        ad_total = 0.0; yr_rev = collections.Counter(); yr_ship = collections.Counter(); yr_ords = collections.Counter()
        for i in range(clock.TOTAL_DAYS):
            ev = run.process_day(s, params, i)
            y = clock.vdate(i).year
            for e in ev:
                if e["type"] == "demand_day":
                    ad_total += e.get("ad_spend", 0)
                if e["type"] == "ledger_order":
                    yr_ords[y] += 1; yr_ship[y] += 1800
    finally:
        supply.DEFECT_RATE, ledger.PRODUCTION_DAYS = od, ol
    L = s["ledger"]; t = L["tot"]
    gross = t["revenue"]; refund = t["refund"]
    net_incl = gross - refund
    net = net_incl / (1 + R["vat_rate"]["v"])
    labor = t.get("labor", 0)
    # 사업주 부담 4대보험(2026-11~12는 2026 요율, 이후 2027 요율; 총액 근사: 월별 지급 합계를 기간 비중으로 나누지 않고 2027 요율 적용 - 해석)
    ins = sum(v["2027"] for k, v in R["employer_insurance"].items() if isinstance(v, dict) and "2027" in v)
    ins_cost = labor * ins
    sev = labor * R["severance_rate"]["v"]
    opex_known = t.get("fee", 0) + t.get("pg", 0)
    ship = sum(yr_ship.values())
    # 광고비·룩북은 장부 지급 합계 안에 있으나 항목 분리 값이 없어 paid_out - (원가성 지급)으로 구하지 않고 별도 합산
    ad = ad_total
    look = sum(p.get("lookbook", 0) for p in params["purchases"] if p.get("order_vdate", "9") <= clock.vdate(clock.TOTAL_DAYS - 1).isoformat())
    op = net - t["cogs"] - opex_known - labor - ins_cost - sev - ship - ad - look
    tb = R["corp_tax"]["brackets"]
    tax = 0.0 if op <= 0 else min(op, tb[0][0]) * tb[0][1] + max(op - tb[0][0], 0) * tb[1][1]
    inv = L["inv_value"]
    return {"orders": t["orders"], "net_rev_ex_vat": net, "cogs": t["cogs"], "fee": t.get("fee", 0), "pg": t.get("pg", 0),
            "labor": labor, "ins": ins_cost, "sev": sev, "ship": ship, "ad": ad, "look": look, "op": op, "tax": tax, "net_after_tax": op - tax, "refund": refund,
            "cash": L["cash"], "min_cash": L["min_cash"], "stock": L["stock"], "inv_value": inv, "lost": t["lost"],
            "discard": t["discard"], "warn": len(s["warnings"])}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    out = {}
    for name, sc in SCEN.items():
        rows = [one(20261101 + k, sc) for k in range(n)]
        out[name] = rows
        print(name, "done", file=sys.stderr)
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
