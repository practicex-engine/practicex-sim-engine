import json
from sweep import agg, BASE
plans = [(10,10),(15,15),(20,20),(30,30),(40,40),(60,60),(100,100)]
out = []
for m in (0.5, 1, 2, 4):
    P = dict(BASE, purchase_conversion=BASE["purchase_conversion"] * m)
    for a, b in plans:
        x = agg(a, b, seeds=range(1, 41), params=P); x["m"] = m; out.append(x)
json.dump(out, open("robust.json", "w"))
print("수요배수 SS/FW  P(현금<0) 평균최저현금(M) 기말현금(M) 체결 미체결 기말재고")
for x in out:
    print(x["m"], "%d/%d" % (x["nss"], x["nfw"]), round(x["p_neg"], 2), round(x["min_cash"]), round(x["cash"]), round(x["filled"]), round(x["lost"]), round(x["stock"]), "매출M", round(x["revenue"]))
