import unittest
from datetime import date

from engine import catalog, clock, ledger as lg, state as st

C = catalog.load()
PUR = [{"id": "SS27", "order_vdate": "2027-01-31", "fabric": 1_000_000, "trims_labor": 3_000_000, "grading": 100_000, "qty": 400,
        "mix": {"티셔츠·탑": 50, "팬츠": 50}}]


def idx(y, m, d):
    return (date(y, m, d) - clock.START).days


def fresh(stock_by_cat, params_extra=None):
    s = {"master_seed": 5, "ledger": lg.new_ledger()}
    L = s["ledger"]
    L["cash"] = lg.INITIAL_CASH
    L["cat_stock"] = dict(stock_by_cat)
    L["cat_value"] = {k: v * C["categories"][k]["unit_cost"] for k, v in stock_by_cat.items()}
    L["stock"] = sum(stock_by_cat.values())
    L["inv_value"] = sum(L["cat_value"].values())
    L["po_done"] = ["SS27", "FW27"]
    L["cat_meta"] = {k: {"base": v, "idx": float(idx(2027, 3, 2)), "reordered": False} for k, v in stock_by_cat.items()}
    p = {"catalog": C, "return_rate": 0.0}
    p.update(params_extra or {})
    return s, p


class CatalogPriceTests(unittest.TestCase):
    def test_price_per_category_not_average(self):
        s, p = fresh({"티셔츠·탑": 500, "팬츠": 500})
        ev = lg.process_day(s, p, idx(2027, 3, 20), 40, PUR)
        orders = [e for e in ev if e["type"] == "ledger_order"]
        self.assertTrue(orders)
        for e in orders:
            self.assertEqual(e["price"], C["categories"][e["cat"]]["list_price"])  # 첫 20일 안이라 할인 없음
            self.assertEqual(e["pg"], round(e["price"] * C["pg_rate"]))

    def test_list_prices_end_with_rule_and_within_tolerance(self):
        for k, c in C["categories"].items():
            self.assertLessEqual(abs(c["list_price"] / c["base_price"] - 1), 0.07, k)
            end = 8000 if c["group"] == "top" else 7000
            if k not in ("맨투맨", "후드티"):
                self.assertEqual(c["list_price"] % 10000, end, k)
        self.assertEqual(C["categories"]["맨투맨"]["list_price"], 98000)

    def test_season_weights_favor_hit_items(self):
        pur = [{"id": "SS27", "mix": {k: 10 for k in C["categories"]}}, {"id": "FW27", "mix": {k: 10 for k in C["categories"]}}]
        ss = catalog.demand_weights(C, pur, date(2027, 4, 1))
        fw = catalog.demand_weights(C, pur, date(2027, 10, 1))
        self.assertGreater(ss["티셔츠·탑"], ss["니트·가디건"])
        self.assertGreater(fw["코트·패딩"], ss["코트·패딩"])
        self.assertEqual(ss["코트·패딩"], 0)

    def test_markdown_by_stock_and_age(self):
        meta = {"base": 100, "idx": 0.0}
        self.assertEqual(catalog.markdown_rate(C, meta, 100, 30), 0.0)
        self.assertEqual(catalog.markdown_rate(C, meta, 100, 60), 0.10)
        self.assertEqual(catalog.markdown_rate(C, meta, 60, 60), 0.0)   # 많이 팔렸으면 할인 없음
        self.assertEqual(catalog.markdown_rate(C, meta, 60, 100), 0.20)
        self.assertEqual(catalog.markdown_rate(C, meta, 45, 150), 0.30)
        self.assertEqual(catalog.markdown_rate(C, meta, 20, 150), 0.0)

    def test_discount_never_below_cost_plus_shipping(self):
        s, p = fresh({"코트·패딩": 1000})
        s["ledger"]["cat_meta"]["코트·패딩"]["idx"] = float(idx(2027, 1, 1))
        s["ledger"]["cat_value"]["코트·패딩"] = 1000 * 250000.0  # 원가가 높은 재고
        s["ledger"]["inv_value"] = 1000 * 250000.0
        ev = lg.process_day(s, p, idx(2027, 10, 20), 30, [{"id": "FW27", "order_vdate": "2027-07-31", "qty": 1, "mix": {"코트·패딩": 1}}])
        for e in [e for e in ev if e["type"] == "ledger_order"]:
            self.assertGreaterEqual(e["price"], min(e["list"], 250000 + 1800))


class ReorderLaborTests(unittest.TestCase):
    def test_reorder_at_35_percent_once(self):
        s, p = fresh({"티셔츠·탑": 100})
        s["ledger"]["cat_stock"]["티셔츠·탑"] = 64  # 36% 판매
        s["ledger"]["stock"] = 64
        ev = lg.process_day(s, p, idx(2027, 4, 1), 0, [{"id": "SS27", "order_vdate": "2027-01-31", "qty": 1, "mix": {"티셔츠·탑": 1}, "fabric": 100, "trims_labor": 300}])
        ro = [e for e in ev if e["type"] == "ledger_reorder"]
        self.assertEqual(len(ro), 1)
        self.assertEqual(ro[0]["qty"], 36)
        ev2 = lg.process_day(s, p, idx(2027, 4, 2), 0, [])
        self.assertFalse([e for e in ev2 if e["type"] == "ledger_reorder"])
        arr = [a for a in s["ledger"]["arrivals"] if a.get("cat") == "티셔츠·탑"]
        self.assertEqual(arr[0]["idx"], idx(2027, 4, 1) + 20)

    def test_no_reorder_below_trigger(self):
        s, p = fresh({"티셔츠·탑": 100})
        s["ledger"]["cat_stock"]["티셔츠·탑"] = 70
        s["ledger"]["stock"] = 70
        ev = lg.process_day(s, p, idx(2027, 4, 1), 0, [])
        self.assertFalse([e for e in ev if e["type"] == "ledger_reorder"])

    def test_labor_min_wage_by_year_once_per_month(self):
        HC = C["labor"]["headcount"]
        s, p = fresh({"팬츠": 10})
        c0 = s["ledger"]["cash"]
        lg.process_day(s, p, idx(2026, 11, 25), 0, [])
        self.assertEqual(c0 - s["ledger"]["cash"], HC * 10320 * 209)
        lg.process_day(s, p, idx(2026, 11, 26), 0, [])
        self.assertEqual(c0 - s["ledger"]["cash"], HC * 10320 * 209)
        c1 = s["ledger"]["cash"]
        lg.process_day(s, p, idx(2027, 1, 25), 0, [])
        self.assertEqual(c1 - s["ledger"]["cash"], HC * 10700 * 209)
        self.assertEqual(catalog.monthly_labor(C, date(2027, 6, 1)), HC * 10700 * 209)

    def test_pg_refunded_on_return(self):
        s, p = fresh({"팬츠": 10})
        s["ledger"]["returns"].append({"due": 0, "kind": "change_of_mind", "unit_cost": 35000.0, "cat": "팬츠", "paid": 117000, "fee": 0, "pg": 3978})
        s["ledger"]["cat_value"]["팬츠"] += 0
        pg0 = 0
        lg.process_day(s, p, idx(2027, 1, 5), 0, [])
        self.assertEqual(s["ledger"]["tot"]["pg"], -3978)


class ConsistencyTests(unittest.TestCase):
    def test_stock_and_value_equal_sum_of_categories_over_year(self):
        from engine import run as runmod, sync
        params = {"purchase_conversion": 0.033, "organic_visitors_per_day": 180, "external_visitors_per_day": 30, "ad_budget_monthly": 500000,
                  "ad_cpc": 700, "external_fee_rate": 0.30}
        params.update(sync.FIXED_ENGINE_PARAMS)
        params["purchases"] = sync.load_purchases()
        params["approvals"] = []
        params["catalog"] = sync.load_catalog()
        s = st.new_state(1, "2026-11-01T06:00:00+09:00")
        for i in range(clock.TOTAL_DAYS):
            runmod.process_day(s, params, i)
            L = s["ledger"]
            self.assertEqual(L["stock"], sum(L["cat_stock"].values()))
            self.assertAlmostEqual(L["inv_value"], sum(L["cat_value"].values()), delta=1.0)
            self.assertTrue(all(v >= 0 for v in L["cat_stock"].values()))


if __name__ == "__main__":
    unittest.main()
