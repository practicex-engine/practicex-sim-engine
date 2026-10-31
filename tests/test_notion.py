import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from engine import notion


class Fake(BaseHTTPRequestHandler):
    log = []
    fail_429_once = {"left": 0}

    def log_message(self, *a):
        pass

    def _send(self, code, obj, headers=None):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        Fake.log.append((self.path, body, self.headers.get("Authorization"), self.headers.get("Notion-Version")))
        if Fake.fail_429_once["left"] > 0:
            Fake.fail_429_once["left"] -= 1
            return self._send(429, {"message": "rate"}, {"Retry-After": "0"})
        if self.path.endswith("/query"):
            page2 = body.get("start_cursor") == "c2"
            row = lambda name, val, st: {"properties": {
                "파라미터": {"title": [{"plain_text": name}]},
                "값": {"number": val},
                "구분": {"select": {"name": st}},
                "단위": {"rich_text": [{"plain_text": "%"}]},
            }}
            if not page2:
                return self._send(200, {"results": [row("불량률", 0.75, "확정")], "has_more": True, "next_cursor": "c2"})
            return self._send(200, {"results": [row("일 방문자 수", None, "미확인")], "has_more": False})
        if self.path == "/v1/pages":
            return self._send(200, {"id": "x"})
        self._send(404, {})


class NotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_port
        notion.MIN_INTERVAL = 0

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Fake.log.clear()

    def test_pagination_and_params(self):
        c = notion.Client(token="t", base=self.base)
        p = notion.read_params(c)
        self.assertEqual(p["불량률"]["value"], 0.75)
        self.assertIsNone(p["일 방문자 수"]["value"])
        self.assertEqual(len(Fake.log), 2)

    def test_headers_and_ping(self):
        c = notion.Client(token="secret-xyz", base=self.base)
        res = notion.ping(c)
        self.assertEqual(res["confirmed"], 1)
        self.assertEqual(res["unknown"], ["일 방문자 수"])
        path, body, auth, ver = Fake.log[-1]
        self.assertEqual(path, "/v1/pages")
        self.assertEqual(auth, "Bearer secret-xyz")
        self.assertEqual(ver, notion.NOTION_VERSION)
        self.assertEqual(body["parent"]["database_id"], notion.DB["errors"])
        self.assertIn("구분", body["properties"])

    def test_retry_on_429(self):
        Fake.fail_429_once["left"] = 1
        c = notion.Client(token="t", base=self.base)
        notion.read_params(c)
        self.assertGreaterEqual(len(Fake.log), 3)

    def test_missing_token(self):
        import os
        old = os.environ.pop("NOTION_TOKEN", None)
        try:
            with self.assertRaises(notion.NotionError):
                notion.Client()
        finally:
            if old is not None:
                os.environ["NOTION_TOKEN"] = old


if __name__ == "__main__":
    unittest.main()
