import json
import re
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from engine import clock, notion as nt, sync

K = clock.KST


def kst(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=K)


def to_read(props):
    out = {}
    for k, v in props.items():
        v = json.loads(json.dumps(v))
        for key in ("title", "rich_text"):
            if key in v:
                for it in v[key]:
                    it["plain_text"] = it["text"]["content"]
        out[k] = v
    return out


class World:
    """메모리 안의 가짜 Notion."""

    def __init__(self):
        self.dbs = {v: [] for v in nt.DB.values()}
        self.n = 0
        self.add(nt.DB["state"], {"항목": {"title": [{"text": {"content": "가상 시계"}}]}, "상태": {"select": {"name": "미가동"}}, "가상일 인덱스": {"number": 0}})
        self.add(nt.DB["state"], {"항목": {"title": [{"text": {"content": "난수 시드"}}]}, "값": {"rich_text": [{"text": {"content": "미정"}}]}})
        for name, status, val in (("자사몰 자연 유입 방문자", "미확인", None), ("구매 전환율", "미확인", None)):
            self.add(nt.DB["params"], {"파라미터": {"title": [{"text": {"content": name}}]}, "구분": {"select": {"name": status}}, "값": {"number": val}})

    def add(self, db, props):
        self.n += 1
        page = {"id": "p%d" % self.n, "properties": to_read(props)}
        self.dbs[db].append(page)
        return page

    def find(self, pid):
        for rows in self.dbs.values():
            for r in rows:
                if r["id"] == pid:
                    return r

    def rows(self, db, title_prop=None, title=None):
        rs = self.dbs[db]
        if title_prop:
            rs = [r for r in rs if nt.text(r["properties"].get(title_prop)) == title]
        return rs


WORLD = World()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        body = self._body()
        m = re.match(r"/v1/databases/(\w+)/query", self.path)
        if m:
            flt = body.get("filter")
            if flt:
                rows = WORLD.rows(m.group(1), flt["property"], flt["title"]["equals"])
            else:
                rows = WORLD.rows(m.group(1))
            return self._send({"results": rows, "has_more": False})
        if self.path == "/v1/pages":
            page = WORLD.add(body["parent"]["database_id"], body["properties"])
            return self._send({"id": page["id"]})
        self._send({}, 404)

    def do_PATCH(self):
        body = self._body()
        m = re.match(r"/v1/pages/(\w+)", self.path)
        page = WORLD.find(m.group(1)) if m else None
        if not page:
            return self._send({}, 404)
        page["properties"].update(to_read(body["properties"]))
        self._send({"id": page["id"]})


class SyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._rs, cls._mx = clock.RATE_SCHEDULE, clock.MAX_NORMAL_DAYS_PER_RUN
        clock.RATE_SCHEDULE = [(datetime(2000, 1, 1, tzinfo=K), 7)]
        clock.MAX_NORMAL_DAYS_PER_RUN = 2
        cls.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        nt.MIN_INTERVAL = 0
        cls.c = nt.Client(token="t", base="http://127.0.0.1:%d" % cls.srv.server_port)

    @classmethod
    def tearDownClass(cls):
        clock.RATE_SCHEDULE, clock.MAX_NORMAL_DAYS_PER_RUN = cls._rs, cls._mx
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_report_rows_written(self):
        from engine import adstats
        mc = {"type": "month_close", "month": "2030-11", "start": "2030-11-01", "end": "2030-11-30", "revenue": 0, "orders": 0, "lost": 0,
              "refund": 0, "cash": 500_000_000, "stock": 0, "warnings": 0, "payables_open": 0, "purchases_paid": 0, "payables_paid": 0,
              "ad_spend": 0, "vdate": "2030-11-30", "sim": True, "engine_version": "t",
              "approvals": [{"id": "DR-X", "vdate": "2026-11-01", "self": False, "title": "t", "basis": "b", "overrides": "o", "limits": "l", "report": "r"}]}
        late = dict(mc, month="2030-12", start="2030-12-01", end="2030-12-31", vdate="2030-12-31", late=True, approvals=[])
        a = {"type": "ad_efficiency_final", "total": adstats.summarize({"spend": 1e6, "visitors": 1000, "orders": 13, "filled": 12.0, "revenue": 1.5e6, "by_year": {}}),
             "by_year": {}, "vdate": "2028-01-14", "sim": True, "engine_version": "t"}
        n = sync.write_events(self.c, [mc, late, a], kst(2026, 11, 1))
        self.assertEqual(n, 3)
        rows = WORLD.rows(nt.DB["reports"])
        titles = [nt.text(r["properties"]["레포트"]) for r in rows]
        self.assertIn("2030-11 운영 레포트 (SIM)", titles)
        self.assertIn("마감 보고: 광고 비용 대비 전환 효율 (SIM)", titles)
        r = [r for r in rows if nt.text(r["properties"]["레포트"]).startswith("2030-12")][0]
        self.assertIn("늦게 마감", nt.text(r["properties"]["요약"]))

    def test_flow(self):
        c = self.c
        # 미가동 상태 status
        s = sync.cmd_status(c, kst(2026, 11, 1, 6))
        self.assertEqual(s["status"], "미가동")
        self.assertFalse(s["seed_set"])
        self.assertIn("purchase_conversion", s["engine_params_missing"])
        self.assertFalse(s["demand_ready"])
        # 미가동이면 step은 건너뜀
        self.assertIn("skipped", sync.cmd_step(c, kst(2026, 11, 1, 8), False))
        # dry-run init은 쓰지 않음
        before = json.dumps(WORLD.dbs, sort_keys=True)
        sync.cmd_init(c, 123, kst(2026, 11, 1, 6), True, False)
        self.assertEqual(before, json.dumps(WORLD.dbs, sort_keys=True))
        # init
        sync.cmd_init(c, 123, kst(2026, 11, 1, 6), False, False)
        s = sync.cmd_status(c, kst(2026, 11, 1, 6))
        self.assertEqual(s["status"], "가동")
        self.assertTrue(s["seed_set"])
        with self.assertRaises(nt.NotionError):
            sync.cmd_init(c, 1, kst(2026, 11, 1, 6), False, False)
        # 2시간 후 step: 가상 약 0.87일 -> 아직 처리할 날 없음
        r = sync.cmd_step(c, kst(2026, 11, 1, 8), False)
        self.assertEqual(r["processed"], [])
        s = sync.cmd_status(c, kst(2026, 11, 1, 8))
        self.assertEqual(s["virtual_index"], 0)
        # 이월이 저장되어 4시간 후에는 가상 1일이 처리됨(두 번 합쳐 1.75일 -> 1일)
        r = sync.cmd_step(c, kst(2026, 11, 1, 10), False)
        self.assertEqual(r["processed"], ["2026-11-01"])
        s = sync.cmd_status(c, kst(2026, 11, 1, 10))
        self.assertEqual(s["virtual_index"], 1)
        # 수요 파라미터가 비어 경고가 한 번만 기록됨
        warns = WORLD.rows(nt.DB["errors"], "내용", "수요 파라미터 미확인")
        self.assertEqual(len(warns), 1)
        sync.cmd_step(c, kst(2026, 11, 1, 12), False)
        self.assertEqual(len(WORLD.rows(nt.DB["errors"], "내용", "수요 파라미터 미확인")), 1)
        # 수요 파라미터 입력 후에는 로그가 쌓이고, 같은 날 재기록은 중복되지 않음
        for r_ in WORLD.dbs[nt.DB["params"]]:
            name = nt.text(r_["properties"]["파라미터"])
            r_["properties"]["구분"] = {"select": {"name": "제안"}}
            r_["properties"]["값"] = {"number": 100 if name.endswith("방문자") else 2.0}
        sync.cmd_step(c, kst(2026, 11, 1, 16), False)
        logs = WORLD.dbs[nt.DB["demand_log"]]
        self.assertTrue(len(logs) >= 1)
        titles = [nt.text(x["properties"]["가상일"]) for x in logs]
        self.assertEqual(len(titles), len(set(titles)))
        self.assertTrue(logs[0]["properties"]["SIM"]["checkbox"])
        # 누락: 며칠 건너뛰면 따라잡지 않고 누락 로그
        sync.cmd_step(c, kst(2026, 11, 5, 22), False)
        miss = [x for x in WORLD.dbs[nt.DB["errors"]] if nt.sel(x["properties"].get("구분")) == "누락"]
        self.assertTrue(len(miss) >= 1)

    def test_param_mapping(self):
        p = {
            "자사몰 자연 유입 방문자": {"value": 100, "status": "제안", "unit": ""},
            "구매 전환율": {"value": 2.0, "status": "미확인", "unit": "%"},
        }
        e = sync.to_engine_params(p)
        self.assertEqual(e["organic_visitors_per_day"], 100.0)
        self.assertNotIn("purchase_conversion", e)
        p["구매 전환율"]["status"] = "제안"
        self.assertAlmostEqual(sync.to_engine_params(p)["purchase_conversion"], 0.02)


if __name__ == "__main__":
    unittest.main()


class ResetTests(unittest.TestCase):
    def test_reset_archives_only_record_tables(self):
        from engine import sync, notion as nt

        class C:
            def __init__(s):
                s.q, s.arch = [], []

            def query_all(s, db, filter=None):
                s.q.append(db)
                return [{"id": db + "-1"}, {"id": db + "-2"}]

            def archive_page(s, pid):
                s.arch.append(pid)

        c = C()
        out = sync.cmd_reset(c, dry_run=False)
        self.assertEqual(set(out["archived"]), set(sync.RESET_DBS))
        self.assertNotIn(nt.DB["state"], c.q)
        self.assertNotIn(nt.DB["params"], c.q)
        self.assertEqual(len(c.arch), 2 * len(sync.RESET_DBS))
        c2 = C()
        sync.cmd_reset(c2, dry_run=True)
        self.assertEqual(c2.arch, [])


class StatePrecisionTests(unittest.TestCase):
    def test_inner_json_keeps_exact_last_run_and_adstats(self):
        from engine import sync
        st = {"pending_pos": [], "warnings": [], "missed_days": [], "last_run": "2026-11-02T10:12:47+09:00",
              "adstats": {"spend": 5}, "ledger": {"cash": 1}}
        inner = json.loads(sync._inner_json(st))
        self.assertEqual(inner["last_run"], "2026-11-02T10:12:47+09:00")
        self.assertEqual(inner["adstats"], {"spend": 5})
