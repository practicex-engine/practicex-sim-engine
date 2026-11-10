"""새 실행안(수량 축소 + 트래픽·전환 개선 가정) 오프라인 재시뮬레이션. 사용: python3 analysis/plan_v2.py"""
import sys, os, json, copy, statistics as S
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import montecarlo as m
CONV = m.R["conversion"]["base"]
def qty(f):
    def g(p):
        P = copy.deepcopy(p["purchases"]); L = P["purchases"] if isinstance(P, dict) else P
        for x in L:
            x["qty"] = int(round(x["qty"] * f)); x["fabric"] = int(round(x["fabric"] * f)); x["trims_labor"] = int(round(x["trims_labor"] * f))
        p["purchases"] = P
    return g
def comb(*fs):
    def g(p): [f(p) for f in fs]
    return g
def org(v): return lambda p: p.update(organic_visitors_per_day=v)
SC = {
 "A 기준(현재 계획)": {"conv": CONV},
 "B 수량만 60%": {"conv": CONV, "mod": qty(0.6)},
 "C 보수형: 수량60% + 자연x1.5 + 전환1.67%": {"conv": 0.0167, "mod": comb(qty(0.6), org(549))},
 "D 목표형: 수량60% + 자연x2 + 전환2.0%": {"conv": 0.02, "mod": comb(qty(0.6), org(732))},
 "E 목표형 + 반품15%": {"conv": 0.02, "rr": 0.15, "mod": comb(qty(0.6), org(732))},
 "F 수량60% + 목표 미달(개선 없음) + 반품15%": {"conv": CONV, "rr": 0.15, "mod": qty(0.6)},
}
K = ("orders", "net_rev_ex_vat", "op", "cash", "min_cash", "stock", "inv_value", "lost")
out = {}
for k, v in SC.items():
    rows = [m.one(20261101 + i, v) for i in range(20)]
    out[k] = {x: S.mean(r[x] for r in rows) for x in K}
    out[k]["op_min"] = min(r["op"] for r in rows); out[k]["op_max"] = max(r["op"] for r in rows)
    print(k, {a: round(b) for a, b in out[k].items()}, flush=True)
json.dump(out, open(os.path.join(os.path.dirname(__file__), "plan_v2_results.json"), "w"), ensure_ascii=False)
