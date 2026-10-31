import unittest
from datetime import date

from engine import clock, ledger, state as st, run as runmod

PUR = [{"id": "T-1", "order_vdate": "2027-01-31", "fabric": 1_000_000, "trims_labor": 300_000, "grading": 60_000, "qty": 100}]
P = {"aov": 100_000, "return_rate": 0.135, "purchase_conversion": 0.01}


def idx(y, m, d):
    return (date(y, m, d) - clock.START).days


def run_days(s, a, b, orders=0, purchases=PUR):
    ev = []
    for i in range(a, b + 1):
        ev += ledger.process_day(s, P, i, orders(i) if callable(orders) else orders, purchases)
        ev += ledger.close_month(s, i)
    return ev


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.s = st.new_state(7, "2026-11-01T00:00:00+09:00")

    def test_fabric_paid_at_order_rest_next_month_10th(self):
        run_days(self.s, 0, idx(2027, 1, 31))
        self.assertEqual(self.s["ledger"]["cash"], 500_000_000 - 1_000_000)
        run_days(self.s, idx(2027, 1, 31) + 1, idx(2027, 2, 9))
        self.assertEqual(self.s["ledger"]["cash"], 500_000_000 - 1_000_000)
        run_days(self.s, idx(2027, 2, 10), idx(2027, 2, 10))
        self.assertEqual(self.s["ledger"]["cash"], 500_000_000 - 1_360_000)

    def test_arrival_after_30_days_with_defects(self):
        run_days(self.s, 0, idx(2027, 1, 31) + 29)
        self.assertEqual(self.s["ledger"]["stock"], 0)
        ev = run_days(self.s, idx(2027, 1, 31) + 30, idx(2027, 1, 31) + 30)
        r = [e for e in ev if e["type"] == "ledger_receipt"][0]
        self.assertEqual(r["qty"] - r["defect_discarded"], self.s["ledger"]["stock"])

    def test_no_stock_means_lost_orders_no_revenue(self):
        run_days(self.s, 0, 30, orders=3, purchases=[])
        L = self.s["ledger"]
        self.assertEqual(L["tot"]["revenue"], 0)
        self.assertEqual(L["tot"]["lost"], 93)
        self.assertEqual(L["cash"], 500_000_000)

    def test_cash_conservation(self):
        end = idx(2027, 4, 30)
        run_days(self.s, 0, end, orders=lambda i: 1 if i > idx(2027, 3, 5) else 0)
        L = self.s["ledger"]
        t = L["tot"]
        sold = t["orders"]
        # 변심 반품은 환불(객단가-3,600)+택배 3,600 = 객단가, 불량은 객단가+3,600 이므로 불량 건만 3,600을 더 낸다
        defect = t["discard"]
        expected_cash = 500_000_000 - t["paid_out"] + t["revenue"] - sold * ledger.SHIPPING - t["refund"] - defect * ledger.ROUND_TRIP
        self.assertEqual(L["cash"], expected_cash)

    def test_month_close_once_per_month(self):
        ev = run_days(self.s, 0, idx(2027, 1, 31))
        months = [e["month"] for e in ev if e["type"] == "month_close"]
        self.assertEqual(months, ["2026-11", "2026-12", "2027-01"])

    def test_purchase_not_double_applied(self):
        run_days(self.s, idx(2027, 1, 31), idx(2027, 1, 31))
        run_days(self.s, idx(2027, 1, 31), idx(2027, 1, 31))
        self.assertEqual(self.s["ledger"]["cash"], 500_000_000 - 1_000_000)

    def test_cash_negative_event(self):
        big = [dict(PUR[0], fabric=600_000_000)]
        ev = run_days(self.s, 0, idx(2027, 2, 1), purchases=big)
        self.assertTrue(any(e["type"] == "cash_negative" for e in ev))


if __name__ == "__main__":
    unittest.main()


class ApprovalTests(unittest.TestCase):
    def test_month_close_carries_approvals_of_that_month(self):
        s = st.new_state(7, "2026-11-01T00:00:00+09:00")
        ap = [{"id": "AP-1", "vdate": "2026-11-01", "title": "t"}, {"id": "AP-2", "vdate": "2026-12-05", "title": "u"}]
        ev = []
        for i in range(0, 61):
            ledger.process_day(s, P, i, 0, [])
            ev += ledger.close_month(s, i, ap)
        mc = {e["month"]: e for e in ev if e["type"] == "month_close"}
        self.assertEqual([a["id"] for a in mc["2026-11"]["approvals"]], ["AP-1"])
        self.assertEqual([a["id"] for a in mc["2026-12"]["approvals"]], ["AP-2"])


class CatchUpTests(unittest.TestCase):
    """누락된 날에 걸린 만기·입고·환불·발주·월말이 사라지지 않는다 (v0.6.1)."""

    def setUp(self):
        self.s = st.new_state(7, "2026-11-01T00:00:00+09:00")

    def test_skipped_order_day_still_places_order(self):
        o = idx(2027, 1, 31)
        run_days(self.s, 0, o - 1)
        ev = run_days(self.s, o + 2, o + 2)  # 발주일(o), o+1이 누락
        self.assertTrue([e for e in ev if e["type"] == "ledger_po"])
        # 입고는 발주일 기준 30일 뒤
        self.assertEqual(self.s["ledger"]["arrivals"][0]["idx"], o + 30)

    def test_skipped_arrival_day_receives_later(self):
        o = idx(2027, 1, 31)
        run_days(self.s, 0, o + 29)
        run_days(self.s, o + 31, o + 31)  # o+30 누락
        self.assertGreater(self.s["ledger"]["stock"], 0)
        self.assertEqual(self.s["ledger"]["arrivals"], [])

    def test_skipped_payable_day_paid_later(self):
        run_days(self.s, 0, idx(2027, 2, 9))
        before = self.s["ledger"]["cash"]
        run_days(self.s, idx(2027, 2, 11), idx(2027, 2, 11))  # 2/10 누락
        self.assertEqual(before - self.s["ledger"]["cash"], 360_000)

    def test_stale_month_closed_late(self):
        from engine import run as r
        s = self.s
        P2 = {"purchase_conversion": 0.0}
        for i in range(0, 30):
            r.process_day(s, P2, i)            # 11/1..11/30 (11/30은 월말이라 정상 마감)
        s2 = st.new_state(7, "2026-11-01T00:00:00+09:00")
        for i in range(0, 29):
            r.process_day(s2, P2, i)
        ev = r.process_day(s2, P2, 31)          # 11/30(인덱스 29), 12/1(30) 누락 -> 12/2 처리
        late = [e for e in ev if e["type"] == "month_close"]
        self.assertEqual(len(late), 1)
        self.assertEqual(late[0]["month"], "2026-11")
        self.assertTrue(late[0]["late"])
        self.assertEqual(late[0]["end"], "2026-11-30")
