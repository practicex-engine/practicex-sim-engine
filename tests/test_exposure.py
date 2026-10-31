import random
import unittest
from datetime import date

from engine import clock, demand, exposure, state as st

PUR = [{"id": "A", "order_vdate": "2027-01-31", "styles": 35}, {"id": "B", "order_vdate": "2027-07-31", "styles": 30}]
BASE = {"purchase_conversion": 0.0133, "organic_visitors_per_day": 30, "external_visitors_per_day": 50,
        "ad_budget_monthly": 3_000_000, "ad_cpc": 700, "sales_start": "2027-01-01", "ad_start": "2027-02-01", "ad_months": 12, "purchases": PUR}


def idx(y, m, d):
    return (date(y, m, d) - clock.START).days


class ExposureTests(unittest.TestCase):
    def test_off_equals_baseline(self):
        for ex in (None, {"n_ref": 30, "gamma_org": 0, "gamma_ad": 0, "launch_boost": 0}):
            p = dict(BASE)
            if ex:
                p["exposure"] = ex
            self.assertEqual(demand.daily_visitors(p, idx(2027, 5, 1)), demand.daily_visitors(BASE, idx(2027, 5, 1)))

    def test_active_styles_follow_latest_arrival(self):
        self.assertEqual(exposure.active(PUR, date(2027, 3, 1))[0], 0)
        self.assertEqual(exposure.active(PUR, date(2027, 3, 2))[0], 35)
        self.assertEqual(exposure.active(PUR, date(2027, 8, 29))[0], 35)
        self.assertEqual(exposure.active(PUR, date(2027, 8, 30))[0], 30)

    def test_gamma_scales_visitors(self):
        p = dict(BASE, exposure={"n_ref": 30, "gamma_org": 0.5, "gamma_ad": 0.5})
        v = demand.daily_visitors(p, idx(2027, 4, 1))
        b = demand.daily_visitors(BASE, idx(2027, 4, 1))
        r = (35 / 30) ** 0.5
        self.assertAlmostEqual(v["organic"], b["organic"] * r)
        self.assertAlmostEqual(v["ad"], b["ad"] * r)

    def test_no_styles_no_visitors_when_on(self):
        p = dict(BASE, exposure={"n_ref": 30, "gamma_org": 0.5, "gamma_ad": 0.5})
        v = demand.daily_visitors(p, idx(2027, 2, 15))
        self.assertEqual(v["organic"] + v["ad"] + v["external"], 0)

    def test_launch_boost_decays(self):
        p = dict(BASE, exposure={"n_ref": 35, "gamma_org": 0, "gamma_ad": 0, "launch_boost": 1.0, "tau": 30})
        a = demand.daily_visitors(p, idx(2027, 3, 2))["organic"]
        b = demand.daily_visitors(p, idx(2027, 6, 2))["organic"]
        self.assertAlmostEqual(a, 60.0)
        self.assertLess(b, a)


if __name__ == "__main__":
    unittest.main()
