import os
import random
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import core  # noqa: E402
import lambda_api  # noqa: E402
import service  # noqa: E402
from store import JsonStore  # noqa: E402


class CoreTests(unittest.TestCase):
    def test_no_sales_is_unknown_and_not_low(self):
        a = core.analyze({"sku": "A", "stock": 20, "min_stock": 10, "cost_usd": 5}, 0, 35)
        self.assertIsNone(a["days_left"])
        self.assertEqual(a["status"], core.STATUS_IN)
        self.assertEqual(a["reorder_qty"], 0)

    def test_status_rules(self):
        self.assertEqual(core.compute_status(0, 10, 0), core.STATUS_OUT)
        self.assertEqual(core.compute_status(9, 10, 0), core.STATUS_LOW)
        self.assertEqual(core.compute_status(12, 10, 4), core.STATUS_LOW)  # 3 days left
        self.assertEqual(core.compute_status(12, 10, 1), core.STATUS_IN)

    def test_reorder(self):
        self.assertEqual(core.target_stock(10, 3.1), 31)
        self.assertEqual(core.reorder_qty(11, 10, 3.1), 20)
        self.assertEqual(core.reorder_qty(100, 10, 3.1), 0)

    def test_fx_threshold(self):
        self.assertTrue(core.fx_exceeds(35.8, 35.0))
        self.assertFalse(core.fx_exceeds(35.5, 35.0))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = JsonStore(os.path.join(self.tmp.name, "d.json"))
        patch = mock.patch.object(service, "get_store", lambda: self.store)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.mail = []
        p = mock.patch.object(service, "send_email", lambda s, m: self.mail.append(s))
        p.start()
        self.addCleanup(p.stop)
        service.refresh_rate(self.store, fetch=lambda: 35.0)
        service.seed(self.store)

    def test_no_duplicate_alerts(self):
        for _ in range(3):
            service.simulate_day(self.store, rng=random.Random(1))
        first = service.run_check(self.store)
        self.assertGreater(len(first["alerts"]), 0)
        n = len(self.mail)
        second = service.run_check(self.store)
        self.assertEqual(second["alerts"], [])
        self.assertEqual(len(self.mail), n)

    def test_recovery_resets_then_realerts(self):
        service.sell("KB-001", 14, self.store)
        service.run_check(self.store)
        service.restock("KB-001", 100, self.store)
        service.run_check(self.store)
        self.assertEqual(self.store.get_product("KB-001")["last_status"], core.STATUS_IN)
        service.sell("KB-001", 100, self.store)
        again = service.run_check(self.store)
        self.assertTrue(any(a["sku"] == "KB-001" for a in again["alerts"]))

    def test_api_down_keeps_last_rate_and_marks_stale(self):
        def boom():
            raise OSError("down")

        r = service.refresh_rate(self.store, fetch=boom)
        self.assertEqual(r["rate"], 35.0)
        self.assertTrue(r["stale"])
        r = service.refresh_rate(self.store, fetch=lambda: 36.0)
        self.assertFalse(r["stale"])

    def test_fx_alert_once(self):
        service.refresh_rate(self.store, fetch=lambda: 36.0)  # +2.86%
        first = service.run_check(self.store)
        self.assertTrue(any(a["type"] == "FX_COST" for a in first["alerts"]))
        second = service.run_check(self.store)
        self.assertFalse(any(a["type"] == "FX_COST" for a in second["alerts"]))

    def test_no_sales_products_do_not_error(self):
        service.create_product({"sku": "x1", "name": "X", "stock": 5, "min_stock": 2, "cost_usd": 1}, self.store)
        d = service.dashboard(self.store)
        x = next(p for p in d["products"] if p["sku"] == "X1")
        self.assertIsNone(x["days_left"])
        service.run_check(self.store)

    def test_sell_validation(self):
        with self.assertRaises(service.ApiError):
            service.sell("KB-001", 999, self.store)


class ApiTests(unittest.TestCase):
    def test_lambda_event_404_and_cors_free(self):
        out = lambda_api.lambda_handler(
            {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/nope"}, None
        )
        self.assertEqual(out["statusCode"], 404)
        self.assertNotIn("Access-Control-Allow-Origin", out["headers"])


if __name__ == "__main__":
    unittest.main()
