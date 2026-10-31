"""광고 비용 대비 전환 효율 집계 (v0.5).

매일 광고비를 장부 현금에서 빼고(가상 장부), 광고 유입 방문자·주문·귀속 매출을 누적한다.
귀속 매출 = 그날 체결 매출 × (광고 주문 / 전체 주문)  [제안: 재고 부족으로 일부만 체결된 날은 채널별로 비례 배분]
주의: 구매 전환율과 CPC는 입력 가정이므로 CPA·ROAS는 가정에서 나온 값에 표본 오차가 더해진 것이다.
시뮬레이션이 '측정'한 효율이 아니다. 실제 효율은 실제 광고 집행으로만 알 수 있다.
"""
from . import clock, ledger


def new_stats():
    return {"spend": 0.0, "visitors": 0, "orders": 0, "filled": 0.0, "revenue": 0.0, "by_year": {}}


def pre_day(state, dem_events, index):
    """광고비를 현금에서 뺀다. ledger.process_day 앞에서 호출한다. 돌려주는 값은 사건 목록."""
    L = state.setdefault("ledger", ledger.new_ledger())
    spend = sum(e.get("ad_spend", 0.0) for e in dem_events if e["type"] == "demand_day")
    if not spend:
        return []
    amt = int(round(spend))
    L["cash"] -= amt
    L["tot"]["paid_out"] += amt
    L["tot"].setdefault("ad_spend", 0)
    L["tot"]["ad_spend"] += amt
    L["month"].setdefault("ad_spend", 0)
    L["month"]["ad_spend"] += amt
    return [{"type": "ledger_cash", "kind": "ad", "label": "광고비(SIM)", "amount": -amt, "cash_after": L["cash"]}]


def post_day(state, dem_events, rev_delta, fill_delta, index):
    A = state.setdefault("adstats", new_stats())
    for e in dem_events:
        if e["type"] != "demand_day":
            continue
        ch = e["by_channel"]["ad"]
        spend = e.get("ad_spend", 0.0)
        A["spend"] += spend
        A["visitors"] += ch["visitors"]
        A["orders"] += ch["orders"]
        share = ch["orders"] / e["orders"] if e["orders"] else 0.0
        A["filled"] += fill_delta * share
        A["revenue"] += rev_delta * share
        if not spend and not ch["visitors"]:
            continue
        d = clock.vdate(index)
        y = A["by_year"].setdefault(str(d.year), {"spend": 0.0, "visitors": 0, "orders": 0, "filled": 0.0, "revenue": 0.0})
        y["spend"] += spend
        y["visitors"] += ch["visitors"]
        y["orders"] += ch["orders"]
        y["filled"] += fill_delta * share
        y["revenue"] += rev_delta * share


def summarize(a):
    spend, rev, filled = a["spend"], a["revenue"], a["filled"]
    return {
        "광고비": int(round(spend)), "광고 방문자": a["visitors"], "광고 수요 주문": a["orders"],
        "귀속 체결 주문": round(filled, 1), "귀속 매출": int(round(rev)),
        "실현 전환율": (a["orders"] / a["visitors"]) if a["visitors"] else None,
        "실현 CPC": (spend / a["visitors"]) if a["visitors"] else None,
        "CPA(체결 주문당)": (spend / filled) if filled else None,
        "ROAS(매출/광고비)": (rev / spend) if spend else None,
    }


def final_event(state):
    A = state.get("adstats") or new_stats()
    return {"type": "ad_efficiency_final", "total": summarize(A),
            "by_year": {y: summarize(v) for y, v in sorted(A["by_year"].items())}}
