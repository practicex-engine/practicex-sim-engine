import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "analysis"))
import montecarlo as m
from engine import supply, ledger


class T(unittest.TestCase):
    def test_runs_and_restores_constants(self):
        d, p = supply.DEFECT_RATE, ledger.PRODUCTION_DAYS
        r = m.one(1, m.SCEN["현실+복합충격"])
        self.assertEqual((supply.DEFECT_RATE, ledger.PRODUCTION_DAYS), (d, p))
        self.assertGreater(r["orders"], 0)
        self.assertAlmostEqual(r["tax"], 0.0 if r["op"] <= 0 else r["tax"])

    def test_deterministic(self):
        a = m.one(5, m.SCEN["현실 기준(전환1.33%)"]); b = m.one(5, m.SCEN["현실 기준(전환1.33%)"])
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
