import json
import os
import random
import tempfile
import unittest
from datetime import date, datetime, timedelta

from engine import clock, demand, supply
from engine.rng import day_rng
from engine import run as runmod
from engine import state as st

K = clock.KST


def kst(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=K)


class ClockTests(unittest.TestCase):
    def setUp(self):
        self._rs = clock.RATE_SCHEDULE
        self._mx = clock.MAX_NORMAL_DAYS_PER_RUN
        clock.RATE_SCHEDULE = [(datetime(2000, 1, 1, tzinfo=K), 7)]
        clock.MAX_NORMAL_DAYS_PER_RUN = 2

    def tearDown(self):
        clock.RATE_SCHEDULE = self._rs
        clock.MAX_NORMAL_DAYS_PER_RUN = self._mx

    def test_full_day_is_1440_min(self):
        self.assertEqual(clock.active_seconds(kst(2026, 11, 1, 0), kst(2026, 11, 2, 0)), 1440 * 60)

    def test_no_night_stop(self):
        self.assertEqual(clock.active_seconds(kst(2026, 11, 1, 22), kst(2026, 11, 2, 6)), 480 * 60)

    def test_backwards_is_zero(self):
        self.assertEqual(clock.active_seconds(kst(2026, 11, 2, 0), kst(2026, 11, 1, 0)), 0)

    def test_one_real_day_is_7_virtual_days(self):
        s = st.new_state(1, kst(2026, 11, 1, 0).isoformat())
        s["carry_units"] = 0
        total = 0
        for h in range(2, 25, 2):
            t = kst(2026, 11, 1, 0) + timedelta(hours=h)
            idxs, missed = clock.advance(s, t)
            total += len(idxs) + len(missed)
        self.assertEqual(s["virtual_index"], 7)
        self.assertEqual(total, 7)
        self.assertEqual(s["carry_units"], 0)

    def test_total_days_440(self):
        self.assertEqual(clock.TOTAL_DAYS, 440)
        self.assertEqual(clock.vdate(439), date(2028, 1, 15) - timedelta(days=1))

    def test_missed_runs_not_caught_up(self):
        s = st.new_state(1, kst(2026, 11, 1, 0).isoformat())
        idxs, missed = clock.advance(s, kst(2026, 11, 4, 0))  # 실제 3일 = 가상 21일
        self.assertEqual(len(idxs), 1)
        self.assertEqual(len(missed), 20)
        self.assertEqual(s["virtual_index"], 21)


class RateChangeTests(unittest.TestCase):
    def test_rate_x30_after_change_time(self):
        a, b = kst(2026, 11, 1, 0), kst(2026, 11, 2, 0)
        self.assertEqual(clock.active_units(a, b), 86400 * 210)

    def test_straddling_change_uses_both_rates(self):
        a, b = kst(2026, 10, 31, 18, 0), kst(2026, 10, 31, 19, 0)  # 18:00~18:20 x7, 18:20~19:00 x210
        self.assertEqual(clock.active_units(a, b), 20 * 60 * 7 + 40 * 60 * 210)

    def test_before_change_is_old_rate(self):
        self.assertEqual(clock.active_units(kst(2026, 10, 31, 16, 8), kst(2026, 10, 31, 17, 8)), 3600 * 7)

    def test_one_real_day_is_210_virtual_days(self):
        s = st.new_state(1, kst(2026, 11, 1, 0).isoformat())
        s["carry_units"] = 0
        for h in range(1, 25):
            clock.advance(s, kst(2026, 11, 1, 0) + timedelta(hours=h))
        self.assertEqual(s["virtual_index"], 210)
        self.assertEqual(s["carry_units"], 0)

    def test_ten_minute_tick_processes_normally(self):
        s = st.new_state(1, kst(2026, 11, 1, 0).isoformat())
        idxs, missed = clock.advance(s, kst(2026, 11, 1, 0, 10))
        self.assertEqual((len(idxs), len(missed)), (1, 0))
        idxs, missed = clock.advance(s, kst(2026, 11, 1, 0, 50))  # 지연 40분 = 약 5.8일
        self.assertEqual(len(missed), 0)
        self.assertGreaterEqual(len(idxs), 5)

    def test_long_gap_still_missed(self):
        s = st.new_state(1, kst(2026, 11, 1, 0).isoformat())
        idxs, missed = clock.advance(s, kst(2026, 11, 1, 3, 0))
        self.assertEqual(len(idxs), 1)
        self.assertGreater(len(missed), 20)


class RngSupplyTests(unittest.TestCase):
    def test_deterministic(self):
        a = day_rng(7, 3).random()
        b = day_rng(7, 3).random()
        self.assertEqual(a, b)
        self.assertNotEqual(a, day_rng(7, 4).random())

    def test_lead_distribution(self):
        r = random.Random(1)
        n = 100000
        c = {3: 0, 4: 0, 5: 0}
        for _ in range(n):
            c[supply.sample_lead_days(r)] += 1
        self.assertAlmostEqual(c[3] / n, 0.40, delta=0.01)
        self.assertAlmostEqual(c[4] / n, 0.40, delta=0.01)
        self.assertAlmostEqual(c[5] / n, 0.20, delta=0.01)

    def test_defect_rate(self):
        r = random.Random(2)
        n = 400000
        self.assertAlmostEqual(supply.defect_count(r, n) / n, 0.0075, delta=0.0007)

    def test_price_step(self):
        self.assertAlmostEqual(supply.price_factor(date(2027, 1, 30)), 1.0)
        self.assertAlmostEqual(supply.price_factor(date(2027, 1, 31)), 1.017)
        self.assertAlmostEqual(supply.price_factor(date(2028, 1, 15)), 1.017)

    def test_delivery_event(self):
        s = st.new_state(1, kst(2026, 11, 1, 6).isoformat())
        r = random.Random(3)
        po = supply.place_po(s, "PO1", 0, 100, r)
        ev = supply.deliveries_due(s, po["due_idx"], r)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["qty"], ev[0]["defect_discarded"] + ev[0]["usable"])
        self.assertEqual(s["pending_pos"], [])


class DemandTests(unittest.TestCase):
    def test_missing_params_no_orders(self):
        s = st.new_state(1, kst(2026, 11, 1, 6).isoformat())
        self.assertEqual(demand.generate_day({"purchase_conversion": None}, s, random.Random(1)), [])
        self.assertIn("demand_params_missing", s["warnings"])


class DemandV2Tests(unittest.TestCase):
    P = {"purchase_conversion": 0.0133, "organic_visitors_per_day": 30, "external_visitors_per_day": 50,
         "ad_budget_monthly": 3000000, "ad_cpc": 700, "sales_start": "2027-01-01", "ad_start": "2027-02-01", "ad_months": 12}

    def test_gates_by_date(self):
        i = lambda y, m, d: (date(y, m, d) - clock.START).days
        self.assertEqual(sum(demand.daily_visitors(self.P, i(2026, 12, 31)).values()), 0)
        v = demand.daily_visitors(self.P, i(2027, 1, 15))
        self.assertEqual((v["organic"], v["ad"], v["external"]), (30, 0.0, 50))
        v = demand.daily_visitors(self.P, i(2027, 2, 1))
        self.assertAlmostEqual(v["ad"], 3000000 / 700 / 30)
        self.assertEqual(demand.daily_visitors(self.P, i(2028, 1, 15))["ad"] > 0, True)  # 2027-02 + 12개월 = 2028-01까지
        self.assertEqual(demand.daily_visitors(self.P, i(2028, 2, 1))["ad"], 0.0)

    def test_missing_parts_are_zero(self):
        p = {"purchase_conversion": 0.01, "organic_visitors_per_day": 10}
        v = demand.daily_visitors(p, 100)
        self.assertEqual((v["ad"], v["external"]), (0.0, 0.0))

    def test_generate_deterministic_and_channels(self):
        s = st.new_state(1, kst(2026, 11, 1, 6).isoformat())
        idx = (date(2027, 3, 1) - clock.START).days
        a = demand.generate_day(self.P, s, random.Random(7), idx)[0]
        b = demand.generate_day(self.P, s, random.Random(7), idx)[0]
        self.assertEqual(a, b)
        self.assertEqual(a["visitors"], sum(c["visitors"] for c in a["by_channel"].values()))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._rs, self._mx = clock.RATE_SCHEDULE, clock.MAX_NORMAL_DAYS_PER_RUN
        clock.RATE_SCHEDULE = [(datetime(2000, 1, 1, tzinfo=K), 7)]
        clock.MAX_NORMAL_DAYS_PER_RUN = 2

    def tearDown(self):
        clock.RATE_SCHEDULE, clock.MAX_NORMAL_DAYS_PER_RUN = self._rs, self._mx

    def test_init_step_marks_sim(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            runmod.main(["init", "--state", p, "--seed", "5", "--now", "2026-11-01T00:00:00+09:00"])
            params = os.path.join(d, "p.json")
            with open(params, "w") as f:
                json.dump({"visitors_per_day": 100, "purchase_conversion": 0.01}, f)
            runmod.main(["step", "--state", p, "--params", params, "--now", "2026-11-02T00:00:00+09:00"])
            with open(os.path.join(d, "events.jsonl"), encoding="utf-8") as f:
                ev = [json.loads(l) for l in f]
            self.assertTrue(ev and all(e["sim"] for e in ev))
            self.assertEqual(st.load(p)["virtual_index"], 7)


if __name__ == "__main__":
    unittest.main()
