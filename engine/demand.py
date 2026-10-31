"""고객 수요 (v0.2).

방문자는 세 갈래의 합이다.
  자사몰 자연 유입  organic_visitors_per_day
  자사몰 광고 유입  ad_budget_monthly / ad_cpc / 30   (ad_start 이후, ad_months 동안)
  외부몰 유입       external_visitors_per_day
판매 시작일(sales_start) 이전에는 주문을 만들지 않는다. 구매 전환율(purchase_conversion)은 모든 갈래에 같이 적용한다.

검증된 값이 없는 항목은 비워 두면(null) 그 갈래는 0으로 계산하고, 전환율이 없으면 주문 자체를 만들지 않는다.
옛 방식(visitors_per_day 한 값)도 호환용으로 남긴다. 값이 있으면 그 값만 쓴다.
광고비는 ad_schedule(연차별 월 예산, plan/ad_schedule.json)이 있으면 그것을 따른다. 없으면 ad_budget_monthly를 ad_months 동안 쓴다.
광고비 일 지출 = 월 예산 / 30 (방문자 계산과 같은 가정).
"""
from datetime import date

from . import clock, exposure

DAYS_PER_MONTH = 30.0  # 가정: 월 광고비를 30일로 나눈다.


def _d(s):
    return date.fromisoformat(s) if s else None


def _num(params, key):
    v = params.get(key)
    return float(v) if v is not None else None


def _warn(state, key):
    if key not in state["warnings"]:
        state["warnings"].append(key)


def ad_budget(params, day):
    """그날 적용되는 월 광고비. ad_schedule(연차별)이 있으면 그것을, 없으면 ad_budget_monthly(+ad_months 창)를 쓴다."""
    ad_start = _d(params.get("ad_start"))
    if day is None or ad_start is None or day < ad_start:
        return 0.0
    sched = params.get("ad_schedule")
    if sched and sched.get("tiers"):
        months = (day.year * 12 + day.month - 1) - (ad_start.year * 12 + ad_start.month - 1)
        year = months // 12 + 1
        budget = 0.0
        for t in sorted(sched["tiers"], key=lambda t: t["from_year"]):
            if year >= t["from_year"]:
                budget = float(t["monthly"])
        return budget
    budget = _num(params, "ad_budget_monthly") or 0.0
    months = params.get("ad_months")
    if months is not None:
        end_month = ad_start.year * 12 + ad_start.month - 1 + int(months)
        if day.year * 12 + day.month - 1 >= end_month:
            return 0.0
    return budget


def daily_visitors(params, index):
    """그날의 갈래별 방문자 수(소수). index가 None이면 날짜 제한을 적용하지 않는다."""
    day = clock.vdate(index) if index is not None else None
    start = _d(params.get("sales_start"))
    if day is not None and start is not None and day < start:
        return {"organic": 0.0, "ad": 0.0, "external": 0.0}
    organic = _num(params, "organic_visitors_per_day") or 0.0
    external = _num(params, "external_visitors_per_day") or 0.0
    ad = 0.0
    budget, cpc = ad_budget(params, day), _num(params, "ad_cpc")
    if budget and cpc:
        ad = budget / cpc / DAYS_PER_MONTH
    if day is not None:
        f_org, f_ad = exposure.factors(params, day)
        organic, external, ad = organic * f_org, external * f_org, ad * f_ad
    return {"organic": organic, "ad": ad, "external": external}


def _count(x, rng):
    """기대값 x를 정수로: 정수부 + 소수부 확률로 1."""
    n = int(x)
    return n + (1 if rng.random() < x - n else 0)


def generate_day(params, state, rng, index=None):
    if params.get("purchase_conversion") is None:
        _warn(state, "demand_params_missing")
        return []
    p = float(params["purchase_conversion"])
    legacy = _num(params, "visitors_per_day")
    if legacy is not None:
        parts = {"organic": legacy, "ad": 0.0, "external": 0.0}
    else:
        parts = daily_visitors(params, index)
    vis = {k: _count(v, rng) for k, v in parts.items()}
    orders = {k: sum(1 for _ in range(n) if rng.random() < p) for k, n in vis.items()}
    return [{
        "type": "demand_day",
        "ad_spend": ad_budget(params, clock.vdate(index) if index is not None else None) / DAYS_PER_MONTH if legacy is None else 0.0,
        "visitors": sum(vis.values()),
        "orders": sum(orders.values()),
        "by_channel": {k: {"visitors": vis[k], "orders": orders[k]} for k in vis},
    }]
