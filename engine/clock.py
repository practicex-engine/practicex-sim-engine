"""가상 시계.

규칙 (대표님 확정, 24시간 연속 가동, 정지 시간 없음):
  ~ 2026-10-31 18:20 KST : 실제 24시간 = 가상 7일 (가상 1일 = 약 3시간 26분)
  2026-10-31 18:20 KST ~ : 현 속도의 30배 = 실제 24시간 = 가상 210일 (가상 1일 = 약 6분 51초)  [해석: '현 시간대비 30배'를 7일 x 30으로 읽음]
시계는 저장된 값으로 관리하며 승인 대기 중에도 멈추지 않는다. 기간은 가상 2026-11-01 ~ 2028-01-15.

계산은 모두 정수(초 x 가상일/일)로 해서 부동소수점 오차가 쌓이지 않게 한다.
속도가 바뀐 시각 이전의 경과 시간은 옛 속도로, 이후는 새 속도로 계산한다(RATE_SCHEDULE). 실행이 늦어도 속도 변경 때문에 날이 누락되지 않는다.
"""
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ACTIVE_SECONDS_PER_DAY = 24 * 3600  # 86,400
# (적용 시작 시각, 실제 24시간당 가상일 수). 시작 시각 순으로 정렬. 첫 항목의 시각은 시계 시작 이전이면 된다.
RATE_SCHEDULE = [
    (datetime(2000, 1, 1, tzinfo=KST), 7),
    (datetime(2026, 10, 31, 18, 20, tzinfo=KST), 210),
]
VDAYS_PER_ACTIVE_DAY = RATE_SCHEDULE[-1][1]  # 현재 속도(표시용)
# 가상 1일에 해당하는 "초 x 속도" 단위. 86,400 단위가 쌓이면 가상 1일.
UNITS_PER_VDAY = ACTIVE_SECONDS_PER_DAY

START = date(2026, 11, 1)
END = date(2028, 1, 15)
TOTAL_DAYS = (END - START).days  # 440 (해석: 인덱스 0..439를 처리하고 440에서 종료)

# 제안(미확정): 한 번의 실행에서 처리할 수 있는 가상일 수의 상한.
# 이를 넘으면 실행이 누락된 것으로 보고, 나머지 날은 따라잡지 않고 누락 로그에만 남긴다.
MAX_NORMAL_DAYS_PER_RUN = 8  # 210일/일에서는 약 55분 지연까지 정상 처리(옛 값 2는 7일/일 기준)


def vdate(index):
    """가상 날짜 인덱스(0부터)를 날짜로 바꾼다."""
    return START + timedelta(days=index)


def active_seconds(a, b):
    """a부터 b까지의 가동 시간을 정수 초로 돌려준다. 24시간 가동이므로 경과 시간 전체다."""
    if b <= a:
        return 0
    return int(round((b - a).total_seconds()))


def active_units(a, b, schedule=None):
    """a부터 b까지의 가동 단위(초 x 그 시점의 속도). 속도 변경 시각을 걸치면 구간별로 더한다."""
    sched = RATE_SCHEDULE if schedule is None else schedule
    if b <= a:
        return 0
    total = 0
    for k, (t0, rate) in enumerate(sched):
        t1 = sched[k + 1][0] if k + 1 < len(sched) else None
        lo = max(a, t0)
        hi = b if t1 is None else min(b, t1)
        if hi > lo:
            total += active_seconds(lo, hi) * rate
    return total


def advance(state, now):
    """실제 시각 now까지 시계를 전진시킨다.

    state를 직접 갱신하고, 이번 실행에서 처리할 가상일 인덱스 목록과 누락된 인덱스 목록을 돌려준다.
    - state["virtual_index"]: 지금까지 처리가 끝난 가상일 수 (다음에 처리할 날의 인덱스)
    - state["carry_units"]: 아직 한 가상일이 되지 못한 가동 시간 (초 x 7)
    - state["last_run"]: 마지막 실행 시각 (ISO)
    """
    last = datetime.fromisoformat(state["last_run"])
    units = active_units(last, now)
    state["last_run"] = now.astimezone(KST).isoformat()
    state["carry_units"] += units
    days_due = state["carry_units"] // UNITS_PER_VDAY
    state["carry_units"] -= days_due * UNITS_PER_VDAY

    first = state["virtual_index"]
    idxs = [i for i in range(first, first + days_due) if i < TOTAL_DAYS]
    state["virtual_index"] = first + days_due
    missed = []
    if len(idxs) > MAX_NORMAL_DAYS_PER_RUN:
        missed = idxs[:-1]
        idxs = idxs[-1:]
    return idxs, missed


def finished(state):
    return state["virtual_index"] >= TOTAL_DAYS
