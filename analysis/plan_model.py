"""시나리오 A 분석용: 시즌별 스타일 구성 -> 발주액(원단/부자재·공임/그레이딩)·수량.
출처: practiceX_가격기준.xlsx '공임 입력'(총원가, 원부자재, 기타 부대비, 그레이딩 60,000, 기대 수량 119.6)와 '시즌 구성(초안)'(복종별 정수 제안)."""
UNITS = 119.6
GRADING = 60_000
# 복종: (총원가, 원부자재, S/S 스타일 수, F/W 스타일 수)  -- 엑셀 값 그대로
CAT = {
 "티셔츠·탑": (18177, 11700, 24, 9), "블라우스·셔츠": (32386.4117647059, 11689.4117647059, 14, 11),
 "니트·가디건": (38000, 0, 9, 15), "원피스": (54701, 22224, 7, 3), "재킷": (51297.9090909091, 14820.9090909091, 12, 19),
 "코트·패딩": (108372, 60895, 0, 7), "맨투맨": (24077, 15600, 2, 3), "후드티": (27027, 17550, 2, 3),
 "스커트": (33806.2307692308, 13329.2307692308, 12, 7), "팬츠": (35501.3506493507, 12624.3506493507, 18, 23)}

def alloc(season, n):
    """시즌 n개 스타일을 복종 비율대로 최대잔여법으로 배분."""
    i = 2 if season == "SS" else 3
    base = {k: v[i] for k, v in CAT.items()}
    tot = sum(base.values())
    raw = {k: n * b / tot for k, b in base.items()}
    out = {k: int(r) for k, r in raw.items()}
    rest = n - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:rest]:
        out[k] += 1
    return out

def purchase(pid, vdate, season, n):
    a = alloc(season, n)
    fabric = trims_labor = 0.0
    for k, c in a.items():
        total, mat, _, _ = CAT[k]
        f = 0.9 * mat * UNITS * c          # 원단 = 원부자재의 90% (확정 9:1)
        fabric += f
        trims_labor += (total * UNITS * c) - f   # 나머지(부자재10%+공임+기타) 후불
    qty = round(UNITS * n)
    return {"id": pid, "order_vdate": vdate, "fabric": int(round(fabric)), "trims_labor": int(round(trims_labor)),
            "grading": GRADING * n, "qty": qty, "styles": n}
if __name__ == "__main__":
    for s in ("SS", "FW"):
        p = purchase("x", "2027-01-31", s, 100); print(s, p, "합계", p["fabric"]+p["trims_labor"]+p["grading"], "장당", (p["fabric"]+p["trims_labor"]+p["grading"])/p["qty"])
