"""판매 부진 민감도: 방문자·전환율 입력을 바꿨을 때 440일 뒤 소진율과 현금. 값은 가정(what-if)이며 결정이 아니다.
실행: python3 analysis/sell_sweep.py"""
import copy, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import clock, run, state as st, sync

BASE = {"purchase_conversion": 0.0133, "organic_visitors_per_day": 30, "external_visitors_per_day": 50,
        "ad_budget_monthly": 500000, "ad_cpc": 700}

def go(over, seed=20261101):
    params = dict(BASE); params.update(sync.FIXED_ENGINE_PARAMS)
    params["purchases"] = sync.load_purchases(); params["approvals"] = []
    sched = sync.load_ad_schedule()
    if sched: params["ad_schedule"] = sched
    ex = sync.load_exposure()
    if ex: params["exposure"] = ex
    params.update(over)
    s = st.new_state(seed, "2026-11-01T06:00:00+09:00")
    for i in range(clock.TOTAL_DAYS):
        run.process_day(s, params, i)
    L = s["ledger"]
    bought = sum(p["qty"] for p in params["purchases"])
    return {"orders": L["tot"]["orders"], "lost": L["tot"]["lost"], "sell_through_%": round(L["tot"]["orders"] / bought * 100, 1),
            "net_rev_만": round((L["tot"]["revenue"] - L["tot"]["refund"]) / 1e4), "cash_만": round(L["cash"] / 1e4),
            "stock": L["stock"], "inv_만": round(L["inv_value"] / 1e4)}

SCEN = [("기준(현재 입력)", {}),
        ("전환율 2.06% (국내 평균 2021, 2차)", {"purchase_conversion": 0.0206}),
        ("전환율 3.3% (상위 20% 기준선)", {"purchase_conversion": 0.033}),
        ("자연 100 + 외부몰 150 명/일", {"organic_visitors_per_day": 100, "external_visitors_per_day": 150}),
        ("자연 300 + 외부몰 500 명/일", {"organic_visitors_per_day": 300, "external_visitors_per_day": 500}),
        ("자연 300 + 외부몰 500 + 전환 2.06%", {"organic_visitors_per_day": 300, "external_visitors_per_day": 500, "purchase_conversion": 0.0206}),
        ("광고 월 300만 (이전 확정값)", {"ad_budget_monthly": 3000000, "ad_schedule": {"tiers": [{"from_year": 1, "monthly": 3000000}]}})]

if __name__ == "__main__":
    for name, o in SCEN:
        print(name, go(o))
