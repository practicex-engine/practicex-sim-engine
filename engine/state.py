"""엔진 상태 파일(JSON). 실제 운영에서는 이 값을 Notion 표에 두고 읽고 쓴다(v0.1은 로컬 파일)."""
import json
import os
import tempfile

from . import ENGINE_VERSION


def new_state(master_seed, now_iso, scenario="normal"):
    return {
        "engine_version": ENGINE_VERSION,
        "master_seed": master_seed,
        "scenario": scenario,
        "virtual_index": 0,
        "carry_units": 0,
        "last_run": now_iso,
        "pending_pos": [],
        "warnings": [],
        "missed_days": [],
    }


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save(path, state):
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
