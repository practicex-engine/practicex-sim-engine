"""복종별 판매 모델 v0.9 (판매가, 시즌 인기, 재고 연동 할인, 리오더, 인건비).

값과 상태(확정/제안/해석)는 plan/catalog.json에 있다. params["catalog"]가 있을 때만 장부가 이 로직을 쓰고,
없으면 v0.8 로직(객단가 고정)으로 돌아간다. 판매 데이터가 없어 인기 계수·할인 단계·탄력성은 모두 제안값이다.
"""
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plan", "catalog.json")


def load(path=PATH):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def season_of(day):
    """SS=3~8월, FW=9~2월 (해석)."""
    return "SS" if 3 <= day.month <= 8 else "FW"


def season_mix(purchases, season):
    for p in purchases:
        if p.get("mix") and str(p["id"]).upper().startswith(season):
            return p["mix"]
    for p in purchases:
        if p.get("mix"):
            return p["mix"]
    return None


def demand_weights(cat, purchases, day):
    """수요 비중(정규화 전) = 시즌 복종 구성비 × 인기 계수."""
    s = season_of(day)
    mix = season_mix(purchases, s) or {k: 1 for k in cat["categories"]}
    pop = cat["popularity"][s]
    return {k: mix.get(k, 0) * pop.get(k, 1.0) for k in cat["categories"]}


def markdown_rate(cat, meta, stock, index):
    """재고가 시즌 입고 대비 많이 남고 오래됐을수록 단계 할인. meta: base(재고 기준량), idx(평균 입고일 index)."""
    if not meta or meta.get("base", 0) <= 0 or stock <= 0:
        return 0.0
    age = index - meta["idx"]
    ratio = stock / meta["base"]
    best = 0.0
    for t in cat["markdown"]["tiers"]:
        if age >= t["min_age_days"] and ratio >= t["min_remaining"]:
            best = max(best, t["discount"])
    return min(best, cat["markdown"]["max_discount"])


def wage(cat, day):
    w = cat["labor"]["hourly_wage"]
    return w.get(str(day.year), w[max(w)])


def monthly_labor(cat, day):
    L = cat["labor"]
    return int(L["headcount"] * wage(cat, day) * L["monthly_hours"])
