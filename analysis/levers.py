"""판매 부진 원인과 레버별 효과(오프라인). 시드 10개 평균. 사용: python3 analysis/levers.py"""
import sys, os, json, copy, collections, statistics as S
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import montecarlo as m
from engine import clock, run, state as st, sync

CONV = m.R["conversion"]["base"]
def sc(**k): d = {"conv": CONV}; d.update(k); return d
def ads(mult):
    def f(p): p["ad_schedule"] = {"tiers": [{"from_year": t["from_year"], "monthly": t["monthly"] * mult} for t in p["ad_schedule"]["tiers"]]}
    return f
def setp(**kw):
    def f(p): p.update(kw)
    return f
def mkd(days):
    def f(p):
        c = copy.deepcopy(p["catalog"]); [t.__setitem__("min_age_days", d) for t, d in zip(c["markdown"]["tiers"], days)]; p["catalog"] = c
    return f
SC = {
 "기준(전환1.33%)": sc(),
 "자연 방문자 x2 (SNS·콘텐츠·검색)": sc(mod=setp(organic_visitors_per_day=732)),
 "외부몰 방문자 x4 (입점 확대)": sc(mod=setp(external_visitors_per_day=200)),
 "광고비 x6 (월 300만→1,800만 수준)": sc(mod=ads(6)),
 "전환율 2.0% (상세페이지·리뷰·가격)": {"conv": 0.02},
 "전환율 2.0% + 자연 방문자 x2": {"conv": 0.02, "mod": setp(organic_visitors_per_day=732)},
 "할인 조기화 (35/70/105일)": sc(mod=mkd([35, 70, 105])),
 "반품률 20%": sc(rr=0.20),
}
def main():
    out = {}
    for k, v in SC.items():
        rows = [m.one(20261101 + i, v) for i in range(10)]
        out[k] = {x: S.mean(r[x] for r in rows) for x in ("orders", "net_rev_ex_vat", "op", "cash", "stock", "inv_value")}
        print(k, {a: round(b) for a, b in out[k].items()}, flush=True)
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "levers_results.json"), "w"), ensure_ascii=False)
    # 기준 시드의 채널·복종 분해
    params = dict(m.BASE); params["purchase_conversion"] = CONV; params.update(sync.FIXED_ENGINE_PARAMS)
    params["purchases"] = sync.load_purchases(); params["approvals"] = []; params["catalog"] = sync.load_catalog(); params["ad_schedule"] = sync.load_ad_schedule(); params["exposure"] = sync.load_exposure()
    s = st.new_state(20261101, "2026-11-01T06:00:00+09:00")
    vis = collections.Counter(); od = collections.Counter(); cat = collections.Counter(); disc = collections.Counter(); price_n = 0; sellday = 0
    for i in range(clock.TOTAL_DAYS):
        for e in run.process_day(s, params, i):
            if e["type"] == "demand_day":
                for ch, v in e["by_channel"].items(): vis[ch] += v["visitors"]; od[ch] += v["orders"]
            if e["type"] == "ledger_order":
                cat[e["cat"]] += 1
                disc["할인 적용" if e.get("price", 0) < e.get("list_price", e.get("price", 0)) else "정상가"] += 1
    print("방문자", dict(vis), "수요주문", dict(od)); print("복종별 판매", dict(cat)); print("할인", dict(disc)); print("복종별 잔여", s["ledger"]["cat_stock"])
    json.dump({"vis": vis, "od": od, "cat": cat, "cat_stock": s["ledger"]["cat_stock"], "disc": disc}, open(os.path.join(os.path.dirname(__file__), "levers_base.json"), "w"), ensure_ascii=False)
main()
