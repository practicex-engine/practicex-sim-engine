import json
import unittest
from datetime import date

from engine import adstats, clock, demand, state as st, run as runmod, sync

SCHED = {"tiers": [{"from_year": 1, "monthly": 500000}, {"from_year": 2, "monthly": 1000000}, {"from_year": 3, "monthly": 3000000}]}
P = {"ad_start": "2027-02-01", "ad_cpc": 700, "purchase_conversion": 0.0133, "sales_start": "2027-01-01", "ad_schedule": SCHED}


class AdTests(unittest.TestCase):
    def test_tiers_by_ad_year(self):
        f = lambda y, m, d: demand.ad_budget(P, date(y, m, d))
        self.assertEqual(f(2027, 1, 31), 0)
        self.assertEqual(f(2027, 2, 1), 500000)
        self.assertEqual(f(2028, 1, 15), 500000)
        self.assertEqual(f(2028, 2, 1), 1000000)
        self.assertEqual(f(2029, 2, 1), 3000000)
        self.assertEqual(f(2031, 6, 1), 3000000)

    def test_fallback_without_schedule(self):
        p = {"ad_start": "2027-02-01", "ad_budget_monthly": 3000000, "ad_months": 12}
        self.assertEqual(demand.ad_budget(p, date(2027, 6, 1)), 3000000)
        self.assertEqual(demand.ad_budget(p, date(2028, 2, 1)), 0)

    def test_visitors_and_cash(self):
        s = st.new_state(1, "2026-11-01T00:00:00+09:00")
        idx = (date(2027, 3, 1) - clock.START).days
        ev = runmod.process_day(s, dict(P), idx)
        d = [e for e in ev if e["type"] == "demand_day"][0]
        self.assertAlmostEqual(d["ad_spend"], 500000 / 30)
        cash = [e for e in ev if e["type"] == "ledger_cash" and e["kind"] == "ad"][0]
        self.assertEqual(cash["amount"], -round(500000 / 30))
        self.assertEqual(s["ledger"]["tot"]["ad_spend"], round(500000 / 30))

    def test_final_event_on_last_day_only(self):
        s = st.new_state(1, "2026-11-01T00:00:00+09:00")
        ev = runmod.process_day(s, dict(P), clock.TOTAL_DAYS - 1)
        self.assertEqual(len([e for e in ev if e["type"] == "ad_efficiency_final"]), 1)
        ev = runmod.process_day(s, dict(P), clock.TOTAL_DAYS - 2)
        self.assertEqual(len([e for e in ev if e["type"] == "ad_efficiency_final"]), 0)

    def test_full_run_totals_consistent(self):
        s = st.new_state(20261101, "2026-11-01T00:00:00+09:00")
        pur = sync.load_purchases()
        p = dict(P, purchases=pur)
        for i in range(clock.TOTAL_DAYS):
            ev = runmod.process_day(s, p, i)
        fin = [e for e in ev if e["type"] == "ad_efficiency_final"][0]
        exp = sum(500000 / 30 for i in range(clock.TOTAL_DAYS) if clock.vdate(i) >= date(2027, 2, 1))
        self.assertAlmostEqual(fin["total"]["광고비"], exp, delta=1)
        self.assertAlmostEqual(s["ledger"]["tot"]["ad_spend"], exp, delta=400)  # 일별 반올림 오차
        self.assertTrue(fin["total"]["ROAS(매출/광고비)"] is not None)
        self.assertLessEqual(abs(sum(v["광고비"] for v in fin["by_year"].values()) - fin["total"]["광고비"]), 3)


if __name__ == "__main__":
    unittest.main()
