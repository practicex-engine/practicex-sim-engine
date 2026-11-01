"""엔진 <-> Notion 동기화.

Notion '엔진 상태' 표에서 시계 상태를 읽고, '엔진 파라미터' 표에서 가정값을 읽어
가상 시계를 전진시킨 뒤 결과(상태, 수요 로그, 이상 상황, 누락·경고 로그)를 Notion에 쓴다.

  python -m engine.sync status [--now ISO]
  python -m engine.sync init --seed N [--now ISO] [--dry-run] [--force]
  python -m engine.sync step [--now ISO] [--dry-run]

원칙:
- status와 --dry-run은 Notion에 아무것도 쓰지 않는다.
- init은 시계를 시작시킨다. 시드는 반드시 명시한다(임의 값을 채우지 않는다).
- 수요 파라미터가 '미확인'이거나 비어 있으면 주문을 만들지 않고 경고만 남긴다.
- 사건 행은 제목으로 중복을 확인하고 쓴다(재실행해도 같은 행이 두 번 생기지 않게).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import ENGINE_VERSION, clock
from . import notion as nt
from .run import step as engine_step

K_CLOCK = "가상 시계"
K_SEED = "난수 시드"
K_CARRY = "시계 이월(carry)"
K_JSON = "엔진 내부 JSON"

# (Notion 파라미터 이름, 엔진 키, 배율). 배율은 Notion 단위 -> 엔진 단위.
PARAM_MAP = (
    ("구매 전환율", "purchase_conversion", 0.01),  # Notion은 %, 엔진은 비율
    ("자사몰 자연 유입 방문자", "organic_visitors_per_day", 1.0),
    ("외부몰 유입 방문자", "external_visitors_per_day", 1.0),
    ("광고비 (월)", "ad_budget_monthly", 1.0),
    ("광고 CPC 가정", "ad_cpc", 1.0),
)
REQUIRED_KEYS = ("purchase_conversion",)  # 없으면 주문을 만들지 않음
# 날짜·기간 값은 확정/제안 문서값이라 코드에 둔다. 바꾸면 이 줄과 지침을 같이 고친다.
FIXED_ENGINE_PARAMS = {"sales_start": "2027-01-01", "ad_start": "2027-02-01", "ad_months": 12}


def parse_now(s):
    if not s:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=clock.KST)


PURCHASES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plan", "purchases.json")


def load_purchases(path=PURCHASES_PATH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("purchases", [])


APPROVALS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plan", "approvals.json")


AD_SCHEDULE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plan", "ad_schedule.json")


def load_ad_schedule(path=AD_SCHEDULE_PATH):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {"tiers": d["tiers"]} if d.get("tiers") else None


def load_approvals(path=APPROVALS_PATH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("approvals", [])


EXPOSURE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plan", "exposure.json")


def load_exposure(path=EXPOSURE_PATH):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("exposure")


def to_engine_params(notion_params):
    out = {}
    for name, key, scale in PARAM_MAP:
        p = notion_params.get(name)
        if p and p["value"] is not None and p["status"] != "미확인":
            out[key] = p["value"] * scale
    out.update(FIXED_ENGINE_PARAMS)
    out["purchases"] = load_purchases()
    out["approvals"] = load_approvals()
    sched = load_ad_schedule()
    if sched:
        out["ad_schedule"] = sched
    ex = load_exposure()
    if ex:
        out["exposure"] = ex
    return out


def _vdate_for_index(index):
    return clock.vdate(min(index, clock.TOTAL_DAYS)).isoformat()


# ---------- 읽기 ----------

def load(client):
    rows = client.query_all(nt.DB["state"])
    by = {}
    for r in rows:
        by[nt.text(r["properties"].get("항목"))] = r
    if K_CLOCK not in by:
        raise nt.NotionError("'%s' 행이 없습니다. 엔진 상태 표를 확인하세요." % K_CLOCK)
    c = by[K_CLOCK]["properties"]
    status = nt.sel(c.get("상태"))
    seed_txt = nt.text(by[K_SEED]["properties"].get("값")) if K_SEED in by else ""
    seed = int(seed_txt) if seed_txt.strip().lstrip("-").isdigit() else None
    carry_txt = nt.text(by[K_CARRY]["properties"].get("값")) if K_CARRY in by else ""
    carry = int(carry_txt) if carry_txt.strip().isdigit() else 0
    inner = {}
    if K_JSON in by:
        raw = nt.text(by[K_JSON]["properties"].get("값"))
        inner = json.loads(raw) if raw.strip() else {}
    idx = nt.num(c.get("가상일 인덱스"))
    state = {
        "engine_version": ENGINE_VERSION,
        "master_seed": seed,
        "virtual_index": int(idx) if idx is not None else 0,
        "carry_units": carry,
        "last_run": nt.date_start(c.get("실제 마지막 실행")),
        "pending_pos": inner.get("pending_pos", []),
        "warnings": inner.get("warnings", []),
        "missed_days": inner.get("missed_days", []),
    }
    if "ledger" in inner:
        state["ledger"] = inner["ledger"]
    ids = {k: by[k]["id"] for k in (K_CLOCK, K_SEED, K_CARRY, K_JSON) if k in by}
    return state, status, ids


# ---------- 쓰기 ----------

def _clock_props(state, status):
    last = datetime.fromisoformat(state["last_run"]) if state.get("last_run") else None
    props = {
        "가상일 인덱스": {"number": state["virtual_index"]},
        "가상 날짜": {"date": {"start": _vdate_for_index(state["virtual_index"])}},
        "상태": {"select": {"name": status}},
        "값": {"rich_text": nt.rich("가상 %s (인덱스 %d/%d)" % (_vdate_for_index(state["virtual_index"]), state["virtual_index"], clock.TOTAL_DAYS))},
    }
    if last:
        props["실제 마지막 실행"] = {"date": {"start": last.isoformat()}}
    return props


def _inner_json(state):
    return json.dumps(
        {"pending_pos": state["pending_pos"], "warnings": state["warnings"], "missed_days": state["missed_days"],
         **({"ledger": state["ledger"]} if "ledger" in state else {})},
        ensure_ascii=False,
    )


def save(client, ids, state, status):
    client.update_page(ids[K_CLOCK], _clock_props(state, status))
    client.update_page(ids[K_CARRY], {"값": {"rich_text": nt.rich(str(state["carry_units"]))}})
    client.update_page(ids[K_JSON], {"값": {"rich_text": nt.rich(_inner_json(state))}})


def _exists(client, db, title_prop, title):
    flt = {"property": title_prop, "title": {"equals": title}}
    return bool(client.query_all(nt.DB[db], filter=flt))


def _write_once(client, db, title_prop, title, props):
    if _exists(client, db, title_prop, title):
        return False
    client.create_page(nt.DB[db], props)
    return True


def _title(s):
    return {"title": [{"text": {"content": s[:200]}}]}


def _fmt(v, nd=0):
    return "-" if v is None else (format(v, ",.%df" % nd))


def _ad_final_text(e):
    def line(k, t):
        return ("%s: 광고비 %s원, 광고 방문자 %s명, 수요 주문 %s건, 체결 주문 %s건, 귀속 매출 %s원, 실현 전환율 %s, 실현 CPC %s원, CPA %s원, ROAS %s"
                % (k, _fmt(t["광고비"]), _fmt(t["광고 방문자"]), _fmt(t["광고 수요 주문"]), _fmt(t["귀속 체결 주문"], 1), _fmt(t["귀속 매출"]),
                   "-" if t["실현 전환율"] is None else "%.2f%%" % (t["실현 전환율"] * 100), _fmt(t["실현 CPC"]),
                   _fmt(t["CPA(체결 주문당)"]), "-" if t["ROAS(매출/광고비)"] is None else "%.2f배" % t["ROAS(매출/광고비)"]))
    lines = [line("전체", e["total"])] + [line(y + "년", t) for y, t in e["by_year"].items()]
    lines.append("해석 주의: 구매 전환율과 CPC는 입력 가정이라 이 효율은 측정값이 아니라 가정에서 나온 값(표본 오차 포함)이다. "
                 "귀속 매출은 체결 매출을 광고 주문 비중으로 나눈 제안 방식이며 반품 환불 전 총액이다.")
    return "\n".join(lines)


def write_events(client, events, now):
    n = 0
    for e in events:
        d = e["vdate"]
        if e["type"] == "demand_day":
            title = "D %s" % d
            props = {
                "가상일": _title(title),
                "가상 날짜": {"date": {"start": d}},
                "방문자": {"number": e["visitors"]},
                "주문 건수": {"number": e["orders"]},
                "SIM": {"checkbox": True},
                "엔진 버전": {"rich_text": nt.rich(e["engine_version"])},
            }
            n += _write_once(client, "demand_log", "가상일", title, props)
        elif e["type"] == "price_change":
            title = "원부자재 가격 +%.1f%% (SIM) %s" % (e["step"] * 100, d)
            props = {
                "상황": _title(title),
                "가상 날짜": {"date": {"start": d}},
                "유형": {"select": {"name": "가격 변동"}},
                "담당 팀": {"select": {"name": "생산·구매"}},
                "처리 상태": {"select": {"name": "발생"}},
                "내용": {"rich_text": nt.rich("누적 가격 배수 %.4f" % e["factor_after"])},
            }
            n += _write_once(client, "anomalies", "상황", title, props)
        elif e["type"] == "month_close":
            title = "%s 운영 레포트 (SIM)" % e["month"]
            n_self = len([a for a in e["approvals"] if a.get("self", True)])
            appr_names = " / ".join(a["id"] + " " + a["title"] for a in e["approvals"])
            summary = ("매출 %s원, 체결 %d건, 재고부족 미체결 %d건, 환불 %s원, 현금 %s원, 재고 %d장, 미지급 %s원. "
                       "발주 지급 %s원, 후불 지급 %s원, 광고비 %s원. 자체 승인 %d건, 이달 기록된 지시·승인 %d건%s. "
                       "팀 연결 전이라 발주는 plan/purchases.json에 있는 것만 반영.%s"
                       % (format(e["revenue"], ","), e["orders"], e["lost"], format(e["refund"], ","), format(e["cash"], ","), e["stock"],
                          format(e["payables_open"], ","), format(e["purchases_paid"], ","), format(e["payables_paid"], ","),
                          format(e.get("ad_spend", 0), ","), n_self, len(e["approvals"]),
                          (": " + appr_names) if e["approvals"] else "",
                          " (월말 날이 누락돼 늦게 마감)" if e.get("late") else ""))
            appr_txt = "\n".join("[%s] %s | 근거: %s | 바꾼 확정 사항: %s | 한계: %s | 전문: %s" % (a["id"], a["title"], a["basis"], a["overrides"], a["limits"], a["report"]) for a in e["approvals"]) or "없음"
            props = {
                "레포트": _title(title),
                "가상 월": {"rich_text": nt.rich(e["month"])},
                "기간 시작": {"date": {"start": e["start"]}},
                "기간 끝": {"date": {"start": e["end"]}},
                "매출(원)": {"number": e["revenue"]},
                "주문 건수": {"number": e["orders"]},
                "현금 잔액(원)": {"number": e["cash"]},
                "경고 건수": {"number": e["warnings"]},
                "자체 승인 건수": {"number": n_self},
                "자체 승인 내역": {"rich_text": nt.rich(appr_txt)},
                "요약": {"rich_text": nt.rich(summary)},
                "상태": {"select": {"name": "작성됨"}},
                "SIM": {"checkbox": True},
            }
            try:
                n += _write_once(client, "reports", "레포트", title, props)
            except nt.NotionError as ex:
                write_log(client, "월간 레포트 쓰기 실패 %s" % e["month"], str(ex)[:500], "오류", now, d)
        elif e["type"] == "ad_efficiency_final":
            title = "마감 보고: 광고 비용 대비 전환 효율 (SIM)"
            summary = _ad_final_text(e)
            props = {
                "레포트": _title(title),
                "가상 월": {"rich_text": nt.rich("마감")},
                "기간 시작": {"date": {"start": clock.START.isoformat()}},
                "기간 끝": {"date": {"start": d}},
                "매출(원)": {"number": e["total"]["귀속 매출"]},
                "주문 건수": {"number": int(round(e["total"]["귀속 체결 주문"]))},
                "요약": {"rich_text": nt.rich(summary)},
                "상태": {"select": {"name": "작성됨"}},
                "SIM": {"checkbox": True},
            }
            try:
                n += _write_once(client, "reports", "레포트", title, props)
            except nt.NotionError as ex:
                write_log(client, "마감 광고 효율 보고 쓰기 실패", str(ex)[:500], "오류", now, d)
        elif e["type"] in ("ledger_order", "ledger_return"):
            is_o = e["type"] == "ledger_order"
            title = "SIM-%s-%s%02d" % (d, "" if is_o else "R", e.get("seq", 0))
            props = {
                "주문번호": _title(title),
                "가상 날짜": {"date": {"start": d}},
                "상태": {"select": {"name": "주문" if is_o else "반품"}},
                "채널": {"select": {"name": "자사몰"}},
                "품번": {"rich_text": nt.rich("복종: %s" % (e.get("cat") or "미분류"))},
                "수량": {"number": 1},
                "판매가(원)": {"number": e.get("price", e.get("amount", 0))},
                "정상가(원)": {"number": e.get("price", e.get("amount", 0))},
                "SIM": {"checkbox": True},
            }
            try:
                n += _write_once(client, "orders", "주문번호", title, props)
            except nt.NotionError as ex:
                write_log(client, "주문 원장 쓰기 실패 %s" % d, str(ex)[:300], "경고", now, d)
        elif e["type"] == "cash_negative":
            title = "현금 부족 (SIM) %s" % d[:7]
            props = {
                "상황": _title(title),
                "가상 날짜": {"date": {"start": d}},
                "유형": {"select": {"name": "현금 부족"}},
                "담당 팀": {"select": {"name": "재무회계"}},
                "처리 상태": {"select": {"name": "발생"}},
                "내용": {"rich_text": nt.rich("현금 잔액 %s원" % format(e["cash"], ","))},
            }
            n += _write_once(client, "anomalies", "상황", title, props)
        elif e["type"] == "delivery" and e["defect_discarded"] > 0:
            title = "입고 불량 폐기 %s %s (SIM)" % (e["po_id"], d)
            props = {
                "상황": _title(title),
                "가상 날짜": {"date": {"start": d}},
                "유형": {"select": {"name": "불량"}},
                "담당 팀": {"select": {"name": "생산·구매"}},
                "처리 상태": {"select": {"name": "발생"}},
                "내용": {"rich_text": nt.rich("입고 %d장 중 %d장 폐기" % (e["qty"], e["defect_discarded"]))},
            }
            n += _write_once(client, "anomalies", "상황", title, props)
    return n


def write_log(client, title, detail, kind, now, vdate=None):
    props = {
        "내용": _title(title),
        "구분": {"select": {"name": kind}},
        "내용 상세": {"rich_text": nt.rich(detail)},
        "실제 시각": {"date": {"start": now.astimezone(clock.KST).isoformat()}},
    }
    if vdate:
        props["가상 날짜"] = {"date": {"start": vdate}}
    return _write_once(client, "errors", "내용", title, props)


# ---------- 명령 ----------

def cmd_status(client, now):
    state, status, ids = load(client)
    notion_params = nt.read_params(client)
    eparams = to_engine_params(notion_params)
    out = {
        "status": status,
        "virtual_index": state["virtual_index"],
        "virtual_date": _vdate_for_index(state["virtual_index"]),
        "last_run": state["last_run"],
        "seed_set": state["master_seed"] is not None,
        "carry_units": state["carry_units"],
        "pending_pos": len(state["pending_pos"]),
        "engine_params_ready": sorted(eparams),
        "engine_params_missing": [k for _, k, _ in PARAM_MAP if k not in eparams],
        "demand_ready": all(k in eparams for k in REQUIRED_KEYS),
        "rows_found": sorted(ids),
    }
    if status == "가동" and state["last_run"]:
        import copy
        probe = copy.deepcopy(state)
        idxs, missed = clock.advance(probe, now)
        out["would_process"] = [clock.vdate(i).isoformat() for i in idxs]
        out["would_miss"] = len(missed)
    return out


def cmd_init(client, seed, now, dry_run, force):
    state, status, ids = load(client)
    if status == "가동" and not force:
        raise nt.NotionError("이미 '가동' 상태입니다. 다시 시작하려면 --force를 지정하세요.")
    new = {
        "master_seed": seed,
        "virtual_index": 0,
        "carry_units": 0,
        "last_run": now.astimezone(clock.KST).isoformat(),
        "pending_pos": [],
        "warnings": [],
        "missed_days": [],
    }
    plan = {"action": "init", "seed": seed, "start_real": new["last_run"], "virtual_start": clock.START.isoformat(), "dry_run": dry_run}
    if dry_run:
        return plan
    for key, init_val in ((K_CARRY, "0"), (K_JSON, _inner_json(new))):
        if key not in ids:
            row = client.create_page(nt.DB["state"], {"항목": _title(key), "값": {"rich_text": nt.rich(init_val)}})
            ids[key] = row["id"]
    client.update_page(ids[K_SEED], {"값": {"rich_text": nt.rich(str(seed))}})
    save(client, ids, new, "가동")
    return plan


def cmd_step(client, now, dry_run):
    state, status, ids = load(client)
    if status != "가동":
        return {"action": "step", "skipped": "상태가 '%s'입니다(가동 아님)." % status}
    if state["master_seed"] is None:
        raise nt.NotionError("난수 시드가 없습니다. init을 먼저 실행하세요.")
    if clock.finished(state):
        return {"action": "step", "skipped": "기간이 끝났습니다."}
    eparams = to_engine_params(nt.read_params(client))
    before_warn = set(state["warnings"])
    events, idxs, missed = engine_step(state, eparams, now)
    summary = {
        "action": "step",
        "dry_run": dry_run,
        "processed": [clock.vdate(i).isoformat() for i in idxs],
        "missed": len(missed),
        "events": len(events),
        "virtual_index": state["virtual_index"],
        "warnings": state["warnings"],
    }
    if dry_run:
        return summary
    written = write_events(client, events, now)
    for m in state["missed_days"][-len(missed):] if missed else []:
        write_log(client, "누락 %s" % m["vdate"], m["reason"], "누락", now, m["vdate"])
    for w in state["warnings"]:
        if w not in before_warn:
            miss = [k for _, k, _ in PARAM_MAP if k not in eparams and k in REQUIRED_KEYS]
            write_log(client, "수요 파라미터 미확인", "값이 없어 주문을 만들지 않았습니다. 비어 있는 항목: %s" % ", ".join(miss), "경고", now)
    summary["rows_written"] = written
    save(client, ids, state, "정지" if clock.finished(state) else "가동")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(prog="engine.sync")
    ap.add_argument("cmd", choices=["status", "init", "step"])
    ap.add_argument("--seed", type=int)
    ap.add_argument("--now")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    now = parse_now(a.now)
    try:
        client = nt.Client()
        if a.cmd == "status":
            out = cmd_status(client, now)
        elif a.cmd == "init":
            if a.seed is None:
                print("실패: init에는 --seed가 필요합니다(임의 값을 채우지 않습니다).")
                return 2
            out = cmd_init(client, a.seed, now, a.dry_run, a.force)
        else:
            out = cmd_step(client, now, a.dry_run)
    except nt.NotionError as e:
        print("실패:", e)
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
