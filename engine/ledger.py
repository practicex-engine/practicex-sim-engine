"""가상 장부 v0.1 (현금, 외상매입금, 재고, 매출, 반품).

확정 규칙 (지침):
- 초기 자본 5억 원. 원단은 발주 시 지급, 부자재·공임·기타·그레이딩은 발주월 기준 익월 10일 후불.
- 본생산 입고 30일, 입고 시 불량 0.75% 폐기, 재고는 평균원가. 부가세·법인세 제외.
- 배송비 1,800원/건. 변심 반품률 13.5%, 왕복 택배비 3,600원은 고객 부담(환불액에서 차감), 100% 재판매.
- 불량 반품은 판매의 0.75%, 본사 부담(왕복 3,600원), 재판매 없이 폐기.

해석/제안 (지침에서 미확정이라 표시):
- 주문 1건 = 상품 1장, 결제액 = 객단가(정상가 기준, 할인 미반영). [해석]
- 재고가 모자라면 그 주문은 체결되지 않고 사라진다(백오더 없음). [제안]
- 변심 반품은 판매 후 7일에 환불된다. 불량 반품도 같다. [제안, 미확인]
- PG 수수료는 값을 확인하지 못해 0으로 둔다. 외부몰 수수료·정산 지연도 반영하지 않는다. [미확인]
- 객단가 기본값 129,620원은 연간 200스타일 가중 평균(제안).
- 발주는 plan/purchases.json에 있는 것만 반영한다. 없으면 재고가 없어 매출도 없다.
"""
from datetime import date

from . import clock
from .rng import day_rng
from .supply import defect_count, DEFECT_RATE

INITIAL_CASH = 500_000_000
SHIPPING = 1800
ROUND_TRIP = 3600
PRODUCTION_DAYS = 30
RETURN_DELAY = 7
DEFAULT_AOV = 129_620
DEFAULT_RETURN_RATE = 0.135


def new_ledger():
    return {
        "cash": INITIAL_CASH, "stock": 0, "inv_value": 0, "payables": [], "arrivals": [], "returns": [],
        "po_done": [], "tot": {"revenue": 0, "orders": 0, "lost": 0, "refund": 0, "cogs": 0, "discard": 0, "paid_out": 0},
        "month": _blank_month(), "min_cash": INITIAL_CASH, "cash_neg_days": 0,
    }


def _blank_month():
    return {"revenue": 0, "orders": 0, "lost": 0, "refund": 0, "shipping": 0, "purchases": 0, "payables_paid": 0, "warnings": 0, "ad_spend": 0}


def _split(total, mix):
    """total장을 복종 비율(mix)대로 정수로 나눈다(최대잔여법). 합은 항상 total."""
    keys = [k for k in mix if mix[k] > 0]
    s = float(sum(mix[k] for k in keys)) or 1.0
    raw = {k: total * mix[k] / s for k in keys}
    out = {k: int(raw[k]) for k in keys}
    left = total - sum(out.values())
    for k in sorted(keys, key=lambda k: (raw[k] - out[k], k), reverse=True)[:left]:
        out[k] += 1
    return out


def _mix_of(purchases, pid=None):
    for p in purchases:
        if p.get("mix") and (pid is None or p["id"] == pid):
            return p["mix"]
    return None


def _ensure_cats(L, purchases):
    """복종별 재고(표시용). 총재고와 어긋나거나 없으면 첫 발주의 복종 비율로 다시 나눈다(해석)."""
    cs = L.get("cat_stock")
    if cs is not None and sum(cs.values()) == L["stock"]:
        return
    mix = _mix_of(purchases)
    if not mix:
        return
    L["cat_stock"] = _split(L["stock"], mix) if L["stock"] else {k: 0 for k in mix}


def _pick_cat(L, rng):
    cs = L.get("cat_stock")
    if not cs:
        return None
    tot = sum(cs.values())
    if tot <= 0:
        return None
    u = rng.random() * tot
    acc = 0
    for k in sorted(cs):
        acc += cs[k]
        if u < acc and cs[k] > 0:
            return k
    return max(cs, key=cs.get)


def _idx(d):
    return (d - clock.START).days


def next_month_10th_idx(d):
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return _idx(date(y, m, 10))


def _pay(L, amount, label, index, events, kind):
    L["cash"] -= amount
    L["tot"]["paid_out"] += amount
    L["month"]["payables_paid" if kind == "payable" else "purchases"] += amount
    events.append({"type": "ledger_cash", "kind": kind, "label": label, "amount": -amount, "cash_after": L["cash"]})


def process_day(state, params, index, orders, purchases):
    """하루치 장부를 처리하고 사건 목록을 돌려준다. orders는 그날 수요 주문 수."""
    L = state.setdefault("ledger", new_ledger())
    events = []
    rng = day_rng(state["master_seed"], index, "ledger")
    crng = day_rng(state["master_seed"], index, "cat")
    _ensure_cats(L, purchases)
    aov = float(params.get("aov", DEFAULT_AOV))
    rr = float(params.get("return_rate", DEFAULT_RETURN_RATE))
    day = clock.vdate(index)

    # 1) 발주: 원단 즉시 지급, 나머지는 익월 10일 후불, 입고 30일 뒤
    for p in purchases:
        if p["id"] in L["po_done"] or p["order_vdate"] > day.isoformat():
            continue  # 발주일이 지났는데 아직 안 했으면(그날이 누락됐으면) 처리하는 첫날에 넣는다
        L["po_done"].append(p["id"])
        odate = date.fromisoformat(p["order_vdate"])
        oidx = _idx(odate)
        if p.get("fabric"):
            _pay(L, int(p["fabric"]), "%s 원단(발주 시 지급)" % p["id"], index, events, "purchase")
        due = next_month_10th_idx(odate)
        for key, label in (("trims_labor", "부자재·공임·기타"), ("grading", "그레이딩")):
            if p.get(key):
                L["payables"].append({"due": due, "amount": int(p[key]), "label": "%s %s" % (p["id"], label)})
        total = int(p.get("fabric", 0)) + int(p.get("trims_labor", 0)) + int(p.get("grading", 0))
        L["arrivals"].append({"idx": oidx + PRODUCTION_DAYS, "qty": int(p["qty"]), "cost": total, "id": p["id"], "mix": p.get("mix")})
        events.append({"type": "ledger_po", "id": p["id"], "qty": int(p["qty"]), "total_cost": total})

    # 2) 후불 대금 지급
    keep = []
    for x in L["payables"]:
        if x["due"] <= index:  # 누락된 날에 만기가 낀 대금은 다음 처리일에 지급
            _pay(L, x["amount"], x["label"], index, events, "payable")
        else:
            keep.append(x)
    L["payables"] = keep

    # 3) 입고(불량 폐기 후 평균원가 반영)
    keep = []
    for a in L["arrivals"]:
        if a["idx"] <= index:  # 누락된 날의 입고는 다음 처리일에 들어온다
            bad = defect_count(rng, a["qty"])
            usable = a["qty"] - bad
            L["stock"] += usable
            L["inv_value"] += a["cost"]
            if a.get("mix"):
                cs = L.setdefault("cat_stock", {})
                for k, v in _split(usable, a["mix"]).items():
                    cs[k] = cs.get(k, 0) + v
            events.append({"type": "ledger_receipt", "id": a["id"], "qty": a["qty"], "defect_discarded": bad, "usable": usable})
        else:
            keep.append(a)
    L["arrivals"] = keep

    # 4) 반품 처리(7일 전에 팔린 건)
    keep = []
    rseq = 0
    for r in L["returns"]:
        if r["due"] <= index:  # 누락된 날의 환불도 다음 처리일에 처리
            if r["kind"] == "change_of_mind":
                refund = int(round(aov)) - ROUND_TRIP
                L["cash"] -= refund + ROUND_TRIP  # 환불 + 본사가 낸 왕복 택배비(환불액에서 차감 = 고객 부담)
                L["tot"]["refund"] += int(round(aov))
                L["month"]["refund"] += int(round(aov))
                L["stock"] += 1
                L["inv_value"] += r["unit_cost"]
                cat = r.get("cat") or _pick_cat(L, crng)
                if cat and L.get("cat_stock") is not None:
                    L["cat_stock"][cat] = L["cat_stock"].get(cat, 0) + 1
                rseq += 1
                events.append({"type": "ledger_return", "seq": rseq, "kind": "change_of_mind", "cat": cat, "amount": int(round(aov))})
            else:  # 불량: 전액 환불 + 왕복 택배비 본사 부담, 폐기
                L["cash"] -= int(round(aov)) + ROUND_TRIP
                L["tot"]["refund"] += int(round(aov))
                L["month"]["refund"] += int(round(aov))
                L["tot"]["discard"] += 1
                rseq += 1
                events.append({"type": "ledger_return", "seq": rseq, "kind": "defect", "cat": r.get("cat"), "amount": int(round(aov))})
        else:
            keep.append(r)
    L["returns"] = keep

    # 5) 판매: 재고 한도 안에서만 체결
    fill = min(orders, L["stock"])
    lost = orders - fill
    for _ in range(fill):
        unit_cost = L["inv_value"] / L["stock"]
        L["stock"] -= 1
        L["inv_value"] -= unit_cost
        L["tot"]["cogs"] += unit_cost
        L["cash"] += int(round(aov)) - SHIPPING
        L["month"]["revenue"] += int(round(aov))
        L["month"]["shipping"] += SHIPPING
        L["tot"]["revenue"] += int(round(aov))
        cat = _pick_cat(L, crng)
        if cat:
            L["cat_stock"][cat] -= 1
        events.append({"type": "ledger_order", "seq": _ + 1, "cat": cat, "price": int(round(aov))})
        u = rng.random()
        if u < rr:
            L["returns"].append({"due": index + RETURN_DELAY, "kind": "change_of_mind", "unit_cost": unit_cost, "cat": cat})
        elif u < rr + DEFECT_RATE:
            L["returns"].append({"due": index + RETURN_DELAY, "kind": "defect", "unit_cost": unit_cost, "cat": cat})
    L["tot"]["orders"] += fill
    L["tot"]["lost"] += lost
    L["month"]["orders"] += fill
    L["month"]["lost"] += lost
    if lost:
        events.append({"type": "ledger_lost_orders", "lost": lost, "filled": fill})

    # 6) 현금 경고
    if L["cash"] < L["min_cash"]:
        L["min_cash"] = L["cash"]
    if L["cash"] < 0:
        L["cash_neg_days"] += 1
        L["month"]["warnings"] += 1
        events.append({"type": "cash_negative", "cash": L["cash"]})
    return events


def is_month_end(index):
    d = clock.vdate(index)
    return (clock.vdate(index + 1).month != d.month) or index == clock.TOTAL_DAYS - 1


def _month_event(state, d, approvals):
    L = state["ledger"]
    m = L["month"]
    ym = "%04d-%02d" % (d.year, d.month)
    ev = {
        "type": "month_close", "month": ym,
        "start": d.replace(day=1).isoformat(), "end": d.isoformat(),
        "revenue": m["revenue"], "orders": m["orders"], "lost": m["lost"], "refund": m["refund"],
        "cash": int(round(L["cash"])), "stock": L["stock"], "warnings": m["warnings"],
        "payables_open": sum(x["amount"] for x in L["payables"]),
        "purchases_paid": m["purchases"], "ad_spend": m.get("ad_spend", 0), "payables_paid": m["payables_paid"],
        "approvals": [a for a in approvals if a["vdate"][:7] == ym],
    }
    L["month"] = _blank_month()
    L["closed_month"] = ym
    return ev


def close_stale(state, index, approvals=()):
    """월말 날이 통째로 누락돼 못 닫은 지난달이 있으면, 새 달의 첫 처리일 맨 앞에서 늦게 닫는다. process_day보다 먼저 호출한다."""
    L = state.setdefault("ledger", new_ledger())
    d = clock.vdate(index)
    cur = "%04d-%02d" % (d.year, d.month)
    om = L.get("open_month")
    L["open_month"] = cur
    if om and om != cur and L.get("closed_month") != om:
        y, m = int(om[:4]), int(om[5:])
        last = (date(y + (m == 12), 1 if m == 12 else m + 1, 1) - clock.timedelta(days=1))
        ev = _month_event(state, last, approvals)
        ev["late"] = True
        return [ev]
    return []


def close_month(state, index, approvals=()):
    """달 끝이면 월간 집계 사건을 돌려주고 월 누계를 비운다."""
    if not is_month_end(index):
        return []
    return [_month_event(state, clock.vdate(index), approvals)]
