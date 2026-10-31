"""구색-수요 연결 (D형, 신제품 노출형). 제안, 근거 수치 없음. 기본은 꺼짐(plan/exposure.json이 없거나 gamma가 0이면 v0.3과 같음).

모델:
- 활성 스타일 수 N(일) = 가장 최근에 입고된 발주의 styles (입고일 = 발주일 + 30일). 첫 입고 전에는 0.
- 자연·외부몰 방문자 배수 = (N / n_ref) ** gamma_org   (N=0이면 0)
- 광고 방문자 배수       = (N / n_ref) ** gamma_ad
- 신제품 효과(선택) = 1 + launch_boost * exp(-입고 후 경과일 / tau)  를 두 배수에 곱한다.
n_ref(기준 구색)와 모든 지수는 제안 값이다. 시즌 끝 이월 노출, 품절로 인한 노출 감소는 반영하지 않는다.
"""
import math
from datetime import date, timedelta

from .ledger import PRODUCTION_DAYS


def active(purchases, day):
    """day(date)에 노출되는 (스타일 수, 입고 후 경과일). 없으면 (0, None)."""
    best = None
    for p in purchases or []:
        if not p.get("styles"):
            continue
        arr = date.fromisoformat(p["order_vdate"]) + timedelta(days=PRODUCTION_DAYS)
        if arr <= day and (best is None or arr > best[0]):
            best = (arr, int(p["styles"]))
    if best is None:
        return 0, None
    return best[1], (day - best[0]).days


def factors(params, day):
    """(자연·외부몰 배수, 광고 배수). exposure 설정이 없거나 꺼져 있으면 (1, 1)."""
    ex = params.get("exposure")
    if not ex or not (ex.get("gamma_org") or ex.get("gamma_ad") or ex.get("launch_boost")):
        return 1.0, 1.0
    n, since = active(params.get("purchases"), day)
    if n <= 0:
        return 0.0, 0.0
    ratio = n / float(ex.get("n_ref", 30))
    new = 1.0
    if ex.get("launch_boost"):
        new = 1.0 + float(ex["launch_boost"]) * math.exp(-since / float(ex.get("tau", 30)))
    return ratio ** float(ex.get("gamma_org", 0)) * new, ratio ** float(ex.get("gamma_ad", 0)) * new
