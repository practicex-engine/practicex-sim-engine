"""엔진 실행 진입점.

  python -m engine.run init --state state.json --seed 12345 --now 2026-11-01T06:00:00+09:00
  python -m engine.run step --state state.json --now 2026-11-01T08:30:00+09:00 [--params params.json]

step은 지난 실행 이후의 가동 시간만큼 가상 시계를 전진시키고, 처리한 가상일마다 사건을
events.jsonl에 한 줄씩 기록한다. 모든 사건에는 sim=true가 붙는다.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

from . import ENGINE_VERSION
from . import adstats, clock, demand, ledger, state as st, supply
from .rng import day_rng


def parse_now(s):
    if not s:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=clock.KST)
    return dt


def load_params(path):
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def process_day(state, params, index):
    rng = day_rng(state["master_seed"], index)
    events = []
    events += ledger.close_stale(state, index, params.get("approvals", []))
    events += supply.price_events(index)
    events += supply.deliveries_due(state, index, rng)
    dem = demand.generate_day(params, state, rng, index)
    events += dem
    orders = sum(e["orders"] for e in dem if e["type"] == "demand_day")
    events += adstats.pre_day(state, dem, index)
    L0 = state["ledger"]["tot"]
    rev0, ord0 = L0["revenue"], L0["orders"]
    ext = sum(e.get("by_channel", {}).get("external", {}).get("orders", 0) for e in dem if e["type"] == "demand_day")
    events += ledger.process_day(state, params, index, orders, params.get("purchases", []), ext)
    L1 = state["ledger"]["tot"]
    adstats.post_day(state, dem, L1["revenue"] - rev0, L1["orders"] - ord0, index)
    events += ledger.close_month(state, index, params.get("approvals", []))
    if index == clock.TOTAL_DAYS - 1:
        events.append(adstats.final_event(state))
    d = clock.vdate(index).isoformat()
    for e in events:
        e["vdate"] = d
        e["sim"] = True
        e["engine_version"] = ENGINE_VERSION
    return events


def step(state, params, now):
    idxs, missed = clock.advance(state, now)
    for m in missed:
        state["missed_days"].append({"vdate": clock.vdate(m).isoformat(), "reason": "실행 누락(따라잡지 않음)"})
    events = []
    for i in idxs:
        events += process_day(state, params, i)
    return events, idxs, missed


def main(argv=None):
    ap = argparse.ArgumentParser(prog="engine.run")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("init")
    a.add_argument("--state", required=True)
    a.add_argument("--seed", type=int, required=True)
    a.add_argument("--now", default=None)
    b = sub.add_parser("step")
    b.add_argument("--state", required=True)
    b.add_argument("--now", default=None)
    b.add_argument("--params", default="params.json")
    b.add_argument("--events", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "init":
        now = parse_now(args.now)
        s = st.new_state(args.seed, now.astimezone(clock.KST).isoformat())
        st.save(args.state, s)
        print(json.dumps({"ok": True, "seed": args.seed, "last_run": s["last_run"]}, ensure_ascii=False))
        return 0

    s = st.load(args.state)
    if clock.finished(s):
        print(json.dumps({"ok": True, "finished": True}, ensure_ascii=False))
        return 0
    now = parse_now(args.now)
    events, idxs, missed = step(s, load_params(args.params), now)
    st.save(args.state, s)
    path = args.events or os.path.join(os.path.dirname(os.path.abspath(args.state)), "events.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(json.dumps({
        "ok": True,
        "processed_vdates": [clock.vdate(i).isoformat() for i in idxs],
        "missed": len(missed),
        "events": len(events),
        "virtual_index": s["virtual_index"],
        "warnings": s["warnings"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
