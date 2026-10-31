"""공급 쪽 가상 상대 (원부자재 공급사, 봉제 공장).

확정값 (대표님 지시):
- 원부자재 공급사 납기: 3일 40%, 4일 40%, 5일 20%, 품절 0%
- 불량률 0.75% (폐기)
- 원부자재 가격: 매년 1월 31일 +1.7%
"""
from datetime import date

from .clock import START, vdate

LEAD_DAYS = ((3, 0.40), (4, 0.40), (5, 0.20))
DEFECT_RATE = 0.0075
PRICE_STEP = 0.017


def sample_lead_days(rng):
    r = rng.random()
    acc = 0.0
    for days, p in LEAD_DAYS:
        acc += p
        if r < acc:
            return days
    return LEAD_DAYS[-1][0]


def defect_count(rng, qty):
    """qty장 중 불량 장수. 장당 독립 0.75%."""
    return sum(1 for _ in range(qty) if rng.random() < DEFECT_RATE)


def price_factor(d, inclusive=True):
    """날짜 d의 원부자재 가격 배수.

    해석(미확인): 인상이 1월 31일 당일부터 적용되는지(inclusive=True) 다음 날부터인지는 미정이다.
    기본값은 당일 적용. 시작일(2026-11-01) 이전의 1월 31일은 세지 않는다.
    """
    count = 0
    for y in range(START.year, d.year + 1):
        j = date(y, 1, 31)
        if j <= START:
            continue
        if (j <= d) if inclusive else (j < d):
            count += 1
    return (1 + PRICE_STEP) ** count


def place_po(state, po_id, index, qty, rng):
    """원부자재 발주를 넣고 납기(가상일 인덱스)를 정한다. 품절 확률은 0%."""
    lead = sample_lead_days(rng)
    po = {"po_id": po_id, "ordered_idx": index, "qty": qty, "due_idx": index + lead, "lead_days": lead}
    state["pending_pos"].append(po)
    return po


def deliveries_due(state, index, rng):
    """index일에 도착하는 발주를 처리해 입고 사건 목록을 돌려준다."""
    events = []
    keep = []
    for po in state["pending_pos"]:
        if po["due_idx"] <= index:
            bad = defect_count(rng, po["qty"])
            events.append({
                "type": "delivery",
                "po_id": po["po_id"],
                "qty": po["qty"],
                "defect_discarded": bad,
                "usable": po["qty"] - bad,
                "lead_days": po["lead_days"],
            })
        else:
            keep.append(po)
    state["pending_pos"] = keep
    return events


def price_events(index):
    d = vdate(index)
    if d.month == 1 and d.day == 31 and d > START:
        return [{"type": "price_change", "item": "원부자재", "step": PRICE_STEP, "factor_after": price_factor(d)}]
    return []
