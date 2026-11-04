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
- PG 수수료: catalog가 있으면 자사몰 결제액의 pg_rate(제안 3.4%)를 뺀다. 없으면 0. 정산 지연은 반영하지 않는다. [제안/미확인]
- v0.9: params["catalog"](plan/catalog.json)가 있으면 복종별 판매가, 시즌 인기 비율, 재고 연동 할인, 35% 리오더, 최저임금 인건비를 쓴다(engine/catalog.py).
- 객단가 기본값 129,620원은 연간 200스타일 가중 평균(제안).
- 발주는 plan/purchases.json에 있는 것만 반영한다. 없으면 재고가 없어 매출도 없다.
"""
import math
from datetime import date

from . import catalog, clock
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
    return {"revenue": 0, "orders": 0, "lost": 0, "refund": 0, "shipping": 0, "purchases": 0, "payables_paid": 0, "warnings": 0, "ad_spend": 0, "fee": 0, "pg": 0, "labor": 0, "reorder": 0}


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


def _count(x, rng):
    n = int(x)
    return n + (1 if rng.random() < x - n else 0)


def _receive_cat(L, C, a, usable, index):
    """복종별 실재고·재고가치 입고. 발주 원가는 복종 기준 총원가 비율로 나눈다(해석). 재고 나이는 평균 입고일로 섞는다."""
    cs = L.setdefault("cat_stock", {})
    cv = L.setdefault("cat_value", {})
    meta = L.setdefault("cat_meta", {})
    parts = {a["cat"]: usable} if a.get("cat") else _split(usable, a["mix"])
    w = sum(q * C["categories"][k]["unit_cost"] for k, q in parts.items()) or 1.0
    for k, q in parts.items():
        old = cs.get(k, 0)
        cs[k] = old + q
        cv[k] = cv.get(k, 0.0) + a["cost"] * q * C["categories"][k]["unit_cost"] / w
        m = meta.setdefault(k, {"base": 0, "idx": float(index), "reordered": False})
        tot = old + q
        m["idx"] = (old * m["idx"] + q * index) / tot if tot else float(index)
        m["base"] = tot
        if not a.get("cat"):
            m["reordered"] = False


def _draw_cat(rng, weights, stock, subst):
    keys = sorted(k for k in weights if weights[k] > 0)
    tot = sum(weights[k] for k in keys)
    if tot <= 0:
        return None
    u, acc, c = rng.random() * tot, 0.0, keys[-1]
    for k in keys:
        acc += weights[k]
        if u < acc:
            c = k
            break
    if stock.get(c, 0) > 0:
        return c
    if rng.random() < subst:
        av = [k for k in keys if stock.get(k, 0) > 0]
        t2 = sum(weights[k] for k in av)
        if t2 > 0:
            u, acc = rng.random() * t2, 0.0
            for k in av:
                acc += weights[k]
                if u < acc:
                    return k
            return av[-1]
    return None


def _sell_cat(L, params, C, purchases, index, day, orders, ext_orders, rng, crng, events):
    """복종별 판매. 반환: (체결 수, 손실 수)."""
    md = C["markdown"]
    w = catalog.demand_weights(C, purchases, day)
    wt = sum(w.values()) or 1.0
    meta, cs, cv = L.setdefault("cat_meta", {}), L.setdefault("cat_stock", {}), L.setdefault("cat_value", {})
    disc = {k: catalog.markdown_rate(C, meta.get(k), cs.get(k, 0), index) for k in w}
    up = sum(w[k] * (1 + md["elasticity"] * disc[k]) for k in w) / wt  # 할인으로 늘어나는 수요 배율(제안 탄력성)
    n = _count(orders * up, rng) if orders else 0
    ext_n = min(_count(ext_orders * up, rng), n) if ext_orders else 0
    chans = ["own"] * (n - ext_n) + ["ext"] * ext_n
    crng.shuffle(chans)
    fee_rate = float(params.get("external_fee_rate", 0.0))
    ext_disc = float(params.get("external_discount_rate", 0.0))
    pg_rate = float(C.get("pg_rate", 0.0))
    rr = float(params.get("return_rate", DEFAULT_RETURN_RATE))
    fill = lost = 0
    for ch in chans:
        c = _draw_cat(crng, w, cs, md["substitution"])
        if c is None:
            lost += 1
            continue
        fill += 1
        unit_cost = cv[c] / cs[c]
        cs[c] -= 1
        cv[c] -= unit_cost
        L["stock"] -= 1
        L["inv_value"] -= unit_cost
        L["tot"]["cogs"] += unit_cost
        list_p = C["categories"][c]["list_price"]
        ext = ch == "ext"
        p = list_p * (1 - disc[c]) * ((1 - ext_disc) if ext else 1.0)
        r = md["round_to"]
        p = round(p / r) * r
        price = int(min(list_p, max(p, math.ceil(unit_cost + md["floor_shipping"]))))  # 하한: 총원가+배송비 1,800(확정)
        mall = crng.choice(["무신사", "W컨셉"]) if ext else "자사몰"
        fee = int(round(price * fee_rate)) if ext else 0
        pg = 0 if ext else int(round(price * pg_rate))
        L["cash"] += price - fee - pg - SHIPPING
        L["month"]["revenue"] += price
        L["month"]["shipping"] += SHIPPING
        L["tot"]["revenue"] += price
        if fee:
            L["tot"]["fee"] = L["tot"].get("fee", 0) + fee
            L["month"]["fee"] = L["month"].get("fee", 0) + fee
        if pg:
            L["tot"]["pg"] = L["tot"].get("pg", 0) + pg
            L["month"]["pg"] = L["month"].get("pg", 0) + pg
        events.append({"type": "ledger_order", "seq": fill, "cat": c, "price": price, "list": list_p, "disc": round(disc[c], 3), "channel": mall, "fee": fee, "pg": pg})
        u = rng.random()
        kind = "change_of_mind" if u < rr else ("defect" if u < rr + DEFECT_RATE else None)
        if kind:
            L["returns"].append({"due": index + RETURN_DELAY, "kind": kind, "unit_cost": unit_cost, "cat": c, "paid": price, "fee": fee, "pg": pg, "channel": mall})
    return fill, lost


def _reorder(L, C, purchases, index, events):
    """복종별 판매율이 트리거(35%)에 닿으면 판매량만큼 리오더. 원단 몫은 즉시, 나머지는 익월 10일 지급(첫 발주 비율)."""
    R = C["reorder"]
    meta, cs = L.get("cat_meta", {}), L.get("cat_stock", {})
    p0 = next((p for p in purchases if p.get("fabric")), None)
    tot0 = (p0["fabric"] + p0.get("trims_labor", 0) + p0.get("grading", 0)) if p0 else 0
    fr = (p0["fabric"] / tot0) if tot0 else 0.0
    day = clock.vdate(index)
    for k in sorted(meta):
        m = meta[k]
        if m["base"] <= 0 or (R.get("once_per_season") and m.get("reordered")):
            continue
        sold = m["base"] - cs.get(k, 0)
        if sold < R["min_qty"] or sold / m["base"] < R["trigger_sell_through"]:
            continue
        qty = int(round(sold * R["qty_ratio"]))
        cost = int(round(qty * C["categories"][k]["unit_cost"]))
        now_pay = int(round(cost * fr))
        if now_pay:
            _pay(L, now_pay, "리오더 %s 원단(발주 시 지급)" % k, index, events, "purchase")
            L["month"]["reorder"] += now_pay
        if cost - now_pay:
            L["payables"].append({"due": next_month_10th_idx(day), "amount": cost - now_pay, "label": "리오더 %s 부자재·공임" % k})
            L["month"]["reorder"] += cost - now_pay
        L["arrivals"].append({"idx": index + R["lead_days"], "qty": qty, "cost": cost, "id": "RO-%s" % k, "cat": k})
        m["reordered"] = True
        events.append({"type": "ledger_reorder", "cat": k, "qty": qty, "cost": cost, "sell_through": round(sold / m["base"], 3)})


def _pay_labor(L, C, index, events):
    day = clock.vdate(index)
    lab = C["labor"]
    if day.isoformat() < lab["start"] or day.day < lab["pay_day"]:
        return
    ym = "%04d-%02d" % (day.year, day.month)
    if L.get("labor_paid") == ym:
        return
    L["labor_paid"] = ym
    amt = catalog.monthly_labor(C, day)
    L["cash"] -= amt
    L["tot"]["paid_out"] += amt
    L["tot"]["labor"] = L["tot"].get("labor", 0) + amt
    L["month"]["labor"] = L["month"].get("labor", 0) + amt
    events.append({"type": "ledger_cash", "kind": "labor", "label": "인건비 %d명 × 최저임금 %s원 × %d시간(SIM)" % (lab["headcount"], format(int(catalog.wage(C, day)), ","), lab["monthly_hours"]), "amount": -amt, "cash_after": L["cash"]})


def process_day(state, params, index, orders, purchases, ext_orders=0):
    """하루치 장부를 처리하고 사건 목록을 돌려준다. orders는 그날 수요 주문 수."""
    L = state.setdefault("ledger", new_ledger())
    events = []
    rng = day_rng(state["master_seed"], index, "ledger")
    crng = day_rng(state["master_seed"], index, "cat")
    C = params.get("catalog")
    if not C:
        _ensure_cats(L, purchases)
    aov = float(params.get("aov", DEFAULT_AOV))
    fee_rate = float(params.get("external_fee_rate", 0.0))
    disc = float(params.get("external_discount_rate", 0.0))
    rr = float(params.get("return_rate", DEFAULT_RETURN_RATE))
    day = clock.vdate(index)

    # 1) 발주: 원단 즉시 지급, 나머지는 익월 10일 후불, 입고 30일 뒤
    for p in purchases:
        if p["id"] in L["po_done"] or p["order_vdate"] > day.isoformat():
            continue  # 발주일이 지났는데 아직 안 했으면(그날이 누락됐으면) 처리하는 첫날에 넣는다
        L["po_done"].append(p["id"])
        odate = date.fromisoformat(p["order_vdate"])
        oidx = _idx(odate)
        if p.get("lookbook"):
            _pay(L, int(p["lookbook"]), "%s 룩북 촬영비(발주 시 지급)" % p["id"], index, events, "purchase")
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
            if C and (a.get("mix") or a.get("cat")):
                _receive_cat(L, C, a, usable, index)
            elif a.get("mix"):
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
                paid = int(round(r.get("paid", aov)))
                refund = paid - ROUND_TRIP
                L["cash"] -= refund + ROUND_TRIP  # 환불 + 본사가 낸 왕복 택배비(환불액에서 차감 = 고객 부담)
                L["tot"]["refund"] += paid
                L["month"]["refund"] += paid
                if r.get("fee"):  # 해석: 외부몰은 환불 건의 수수료를 돌려준다(정산 조건 미확인)
                    L["cash"] += r["fee"]
                    L["tot"]["fee"] = L["tot"].get("fee", 0) - r["fee"]
                    L["month"]["fee"] = L["month"].get("fee", 0) - r["fee"]
                if r.get("pg"):  # 해석: 결제 취소 시 PG 수수료도 돌려받는다(PG 계약 조건 미확인)
                    L["cash"] += r["pg"]
                    L["tot"]["pg"] = L["tot"].get("pg", 0) - r["pg"]
                    L["month"]["pg"] = L["month"].get("pg", 0) - r["pg"]
                L["stock"] += 1
                L["inv_value"] += r["unit_cost"]
                L["tot"]["cogs"] -= r["unit_cost"]  # 재판매하는 반품은 매출원가에서 되돌린다
                cat = r.get("cat") or _pick_cat(L, crng)
                if cat and L.get("cat_stock") is not None:
                    L["cat_stock"][cat] = L["cat_stock"].get(cat, 0) + 1
                    if C:
                        L.setdefault("cat_value", {})[cat] = L["cat_value"].get(cat, 0.0) + r["unit_cost"]
                rseq += 1
                events.append({"type": "ledger_return", "seq": rseq, "kind": "change_of_mind", "cat": cat, "amount": paid, "channel": r.get("channel", "자사몰")})
            else:  # 불량: 전액 환불 + 왕복 택배비 본사 부담, 폐기
                paid = int(round(r.get("paid", aov)))
                L["cash"] -= paid + ROUND_TRIP
                L["tot"]["refund"] += paid
                L["month"]["refund"] += paid
                if r.get("fee"):
                    L["cash"] += r["fee"]
                    L["tot"]["fee"] = L["tot"].get("fee", 0) - r["fee"]
                    L["month"]["fee"] = L["month"].get("fee", 0) - r["fee"]
                if r.get("pg"):
                    L["cash"] += r["pg"]
                    L["tot"]["pg"] = L["tot"].get("pg", 0) - r["pg"]
                    L["month"]["pg"] = L["month"].get("pg", 0) - r["pg"]
                L["tot"]["discard"] += 1
                rseq += 1
                events.append({"type": "ledger_return", "seq": rseq, "kind": "defect", "cat": r.get("cat"), "amount": paid})
        else:
            keep.append(r)
    L["returns"] = keep

    # 5) 판매: 재고 한도 안에서만 체결
    if C:
        _pay_labor(L, C, index, events)
        fill, lost = _sell_cat(L, params, C, purchases, index, day, orders, max(0, min(int(ext_orders), orders)), rng, crng, events)
        chans, fill_old = [], 0
    else:
        fill = min(orders, L["stock"])
        lost = orders - fill
        ext_orders = max(0, min(int(ext_orders), orders))
        chans = ["own"] * (orders - ext_orders) + ["ext"] * ext_orders
        crng.shuffle(chans)  # 재고가 모자랄 때 어느 채널 주문이 체결되는지는 무작위(별도 난수열)
        fill_old = fill
    for k in range(fill_old):
        unit_cost = L["inv_value"] / L["stock"]
        L["stock"] -= 1
        L["inv_value"] -= unit_cost
        L["tot"]["cogs"] += unit_cost
        ext = chans[k] == "ext"
        mall = crng.choice(["무신사", "W컨셉"]) if ext else "자사몰"  # 제안: 두 몰에 반반
        price = int(round(aov * (1 - disc))) if ext else int(round(aov))
        fee = int(round(price * fee_rate)) if ext else 0
        L["cash"] += price - fee - SHIPPING
        L["month"]["revenue"] += price
        L["month"]["shipping"] += SHIPPING
        L["tot"]["revenue"] += price
        if fee:
            L["tot"]["fee"] = L["tot"].get("fee", 0) + fee
            L["month"]["fee"] = L["month"].get("fee", 0) + fee
        cat = _pick_cat(L, crng)
        if cat:
            L["cat_stock"][cat] -= 1
        events.append({"type": "ledger_order", "seq": k + 1, "cat": cat, "price": price, "channel": mall, "fee": fee})
        u = rng.random()
        if u < rr:
            L["returns"].append({"due": index + RETURN_DELAY, "kind": "change_of_mind", "unit_cost": unit_cost, "cat": cat, "paid": price, "fee": fee, "channel": mall})
        elif u < rr + DEFECT_RATE:
            L["returns"].append({"due": index + RETURN_DELAY, "kind": "defect", "unit_cost": unit_cost, "cat": cat, "paid": price, "fee": fee, "channel": mall})
    L["tot"]["orders"] += fill
    L["tot"]["lost"] += lost
    L["month"]["orders"] += fill
    L["month"]["lost"] += lost
    if lost:
        events.append({"type": "ledger_lost_orders", "lost": lost, "filled": fill})

    if C:
        _reorder(L, C, purchases, index, events)

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
        "purchases_paid": m["purchases"], "ad_spend": m.get("ad_spend", 0), "fee": m.get("fee", 0), "pg": m.get("pg", 0), "labor": m.get("labor", 0), "reorder": m.get("reorder", 0), "payables_paid": m["payables_paid"],
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
