import json, statistics as S
from sweep import sim, BASE
plans = [(10,10),(20,20),(35,30),(50,50),(75,75),(100,100)]
cases = [("γ=0 (연결 없음)", dict(gamma_org=0, gamma_ad=0, launch_boost=0))] + [("γ=%s" % g, dict(gamma_org=g, gamma_ad=g, launch_boost=0)) for g in (0.25, 0.5, 0.75, 1.0)] + [("γ=0.5+신제품 b=0.5", dict(gamma_org=0.5, gamma_ad=0.5, launch_boost=0.5))]
out = []
for name, ex in cases:
    P = dict(BASE, exposure=dict(n_ref=30, tau=30, **ex))
    for a, b in plans:
        rs = [sim(s, a, b, P) for s in range(1, 41)]
        x = dict(case=name, plan="%d/%d" % (a, b), p_neg=sum(r["neg"] for r in rs)/len(rs), filled=S.mean(r["filled"] for r in rs),
                 revenue=S.mean(r["revenue"] for r in rs)/1e6, cash=S.mean(r["cash"] for r in rs)/1e6, equity=S.mean(r["equity"] for r in rs)/1e6,
                 min_cash=S.mean(r["min_cash"] for r in rs)/1e6, stock=S.mean(r["stock"] for r in rs))
        out.append(x)
json.dump(out, open("expo.json", "w"), ensure_ascii=False)
print("케이스 | 계획 | P(현금<0) | 체결 | 매출M | 기말현금M | 평가자산M(현금+재고원가) | 최저현금M | 기말재고")
for x in out:
    print(x["case"], x["plan"], round(x["p_neg"],2), round(x["filled"]), round(x["revenue"]), round(x["cash"]), round(x["equity"]), round(x["min_cash"]), round(x["stock"]))
