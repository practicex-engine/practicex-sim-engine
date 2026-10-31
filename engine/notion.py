"""Notion API 연결 (표준 라이브러리만 사용).

키는 환경변수 NOTION_TOKEN에서만 읽고 어디에도 출력하지 않는다.
API 주소는 NOTION_API_BASE로 바꿀 수 있다(시험용 가짜 서버).

주의(미확인): Notion-Version과 엔드포인트(/v1/databases/{id}/query)는 공식 문서를 이 환경에서
읽지 못하고 기억에 의존했다. 실제 연결에서 오류가 나면 이 두 가지부터 의심한다.

사용:
  python -m engine.notion ping      # 읽기 1회 + 시험 행 1개 쓰기
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

NOTION_VERSION = "2022-06-28"
DEFAULT_BASE = "https://api.notion.com"
MIN_INTERVAL = 0.4  # 초. 평균 초당 3건 한도(기억, 미확인)보다 느리게 호출한다.
KST = timezone(timedelta(hours=9))

# 데이터베이스 ID (Notion 표 URL의 32자리). 비밀이 아니다.
DB = {
    "state": "e277c9af068446638937ed108741a67d",
    "params": "20fea130dd7d4e9eb5d16f21daf22010",
    "demand_log": "c5cc3b7be9ea4694ad5a8db67a4cc009",
    "orders": "3e99ab9110104c798fcc874c95ea965f",
    "anomalies": "e71860613af6444e88c4064c44ecbbcc",
    "errors": "f314c08083a9415ba03c7c2d3f129e56",
    "reports": "db26345b5a1044ed903458a4a907fece",
}


class NotionError(Exception):
    pass


class Client:
    def __init__(self, token=None, base=None):
        self.token = token or os.environ.get("NOTION_TOKEN")
        if not self.token:
            raise NotionError("NOTION_TOKEN 환경변수가 없습니다.")
        self.base = (base or os.environ.get("NOTION_API_BASE") or DEFAULT_BASE).rstrip("/")
        self._last = 0.0

    def _request(self, method, path, body=None, retries=4):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in range(retries + 1):
            wait = MIN_INTERVAL - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", "Bearer " + self.token)
            req.add_header("Notion-Version", NOTION_VERSION)
            req.add_header("Content-Type", "application/json")
            self._last = time.time()
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                payload = e.read().decode("utf-8", "replace")
                if e.code == 429 and attempt < retries:
                    time.sleep(float(e.headers.get("Retry-After", "1")))
                    continue
                if e.code >= 500 and attempt < retries:
                    time.sleep(1 + attempt)
                    continue
                raise NotionError("HTTP %s %s %s: %s" % (e.code, method, path, payload[:300]))
            except urllib.error.URLError as e:
                if attempt < retries:
                    time.sleep(1 + attempt)
                    continue
                raise NotionError("연결 실패 %s: %s" % (path, e.reason))
        raise NotionError("재시도 초과: " + path)

    def query_all(self, db_id, filter=None):
        rows, cursor = [], None
        while True:
            body = {"page_size": 100}
            if filter:
                body["filter"] = filter
            if cursor:
                body["start_cursor"] = cursor
            res = self._request("POST", "/v1/databases/%s/query" % db_id, body)
            rows += res.get("results", [])
            if not res.get("has_more"):
                return rows
            cursor = res.get("next_cursor")

    def update_page(self, page_id, properties):
        return self._request("PATCH", "/v1/pages/%s" % page_id, {"properties": properties})

    def create_page(self, db_id, properties):
        return self._request("POST", "/v1/pages", {"parent": {"database_id": db_id}, "properties": properties})


def text(prop):
    prop = prop or {}
    items = prop.get("title") or prop.get("rich_text") or []
    return "".join(i.get("plain_text", "") for i in items)


_text = text


def num(prop):
    return (prop or {}).get("number")


def sel(prop):
    s = (prop or {}).get("select")
    return s.get("name") if s else None


def date_start(prop):
    d = (prop or {}).get("date")
    return d.get("start") if d else None


def rich(s, chunk=1900):
    """긴 문자열을 Notion rich_text 조각(2,000자 제한 이하)으로 나눈다."""
    s = s or ""
    parts = [s[i:i + chunk] for i in range(0, len(s), chunk)] or [""]
    return [{"text": {"content": x}} for x in parts]


def read_params(client):
    """엔진 파라미터 표를 읽어 {이름: {값, 구분, 단위}}로 돌려준다. 값이 비면 None."""
    out = {}
    for row in client.query_all(DB["params"]):
        p = row.get("properties", {})
        name = _text(p.get("파라미터", {}))
        if not name:
            continue
        sel = (p.get("구분", {}) or {}).get("select") or {}
        out[name] = {
            "value": (p.get("값", {}) or {}).get("number"),
            "status": sel.get("name"),
            "unit": _text(p.get("단위", {})),
        }
    return out


def log_error(client, title, detail, kind="경고", virtual_date=None):
    props = {
        "내용": {"title": [{"text": {"content": title[:200]}}]},
        "구분": {"select": {"name": kind}},
        "내용 상세": {"rich_text": [{"text": {"content": detail[:1900]}}]},
        "실제 시각": {"date": {"start": datetime.now(KST).isoformat()}},
    }
    if virtual_date:
        props["가상 날짜"] = {"date": {"start": virtual_date}}
    return client.create_page(DB["errors"], props)


def ping(client):
    params = read_params(client)
    confirmed = sum(1 for v in params.values() if v["status"] == "확정")
    unknown = [k for k, v in params.items() if v["status"] == "미확인"]
    log_error(
        client,
        "연결 시험 (SIM)",
        "파라미터 %d행 읽음(확정 %d, 미확인 %d). 이 행은 연결 시험으로 엔진이 쓴 것입니다." % (len(params), confirmed, len(unknown)),
        kind="경고",
    )
    return {"params": len(params), "confirmed": confirmed, "unknown": unknown}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv[:1] != ["ping"]:
        print("사용법: python -m engine.notion ping")
        return 2
    try:
        res = ping(Client())
    except NotionError as e:
        print("실패:", e)
        return 1
    print(json.dumps({"ok": True, **res}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
