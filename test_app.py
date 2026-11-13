import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app


class MarketplaceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = patch.object(app, "DATABASE", str(Path(self.directory.name) / "test.sqlite3"))
        self.database.start()
        app.init_db()
        self.addCleanup(self.database.stop)
        self.addCleanup(self.directory.cleanup)

    def create(self):
        response = {"id": "ORDER123", "links": [
            {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER123"}
        ]}
        with patch.object(app, "paypal_request", return_value=response) as paypal:
            order = app.create_order({
                "name": "Example Customer", "email": "customer@example.com",
                "items": [{"id": 1, "quantity": 2, "price_cents": 1}],
            })
        self.assertEqual(paypal.call_args.args[2]["purchase_units"][0]["amount"]["value"], "25.98")
        return order

    def completed(self, value="25.98"):
        return {"id": "ORDER123", "status": "COMPLETED", "purchase_units": [
            {"payments": {"captures": [{"status": "COMPLETED",
                                        "amount": {"currency_code": "USD", "value": value}}]}}
        ]}

    def test_create_uses_database_prices_and_stores_customer(self):
        order = self.create()
        with app.connect() as db:
            row = db.execute("SELECT orders.*, customers.name, customers.email FROM orders "
                             "JOIN customers ON customers.id = orders.customer_id").fetchone()
        self.assertEqual(row["total_cents"], 2598)
        self.assertEqual(row["email"], "customer@example.com")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["access_hash"], hashlib.sha256(order["access"].encode()).hexdigest())
        self.assertNotEqual(row["access_hash"], order["access"])

    def test_invalid_carts_are_rejected_before_paypal(self):
        for items in ([], [{"id": 999, "quantity": 1}], [{"id": 1, "quantity": -1}],
                      [{"id": 1, "quantity": True}], [{"id": 1, "quantity": 1}] * 2):
            with self.subTest(items=items), patch.object(app, "paypal_request") as paypal:
                with self.assertRaises(ValueError):
                    app.create_order({"name": "Test", "email": "test@example.com", "items": items})
                paypal.assert_not_called()

    def test_capture_checks_access_and_verified_amount_and_is_idempotent(self):
        order = self.create()
        with patch.object(app, "paypal_request", return_value=self.completed("0.01")):
            with self.assertRaises(ValueError):
                app.capture_order(order)
        with patch.object(app, "paypal_request", return_value=self.completed()) as paypal:
            with self.assertRaises(ValueError):
                app.capture_order({"id": order["id"], "access": "wrong"})
            paypal.assert_not_called()
            self.assertEqual(app.capture_order(order), {"status": "paid"})
            self.assertEqual(app.capture_order(order), {"status": "paid"})
            self.assertEqual(paypal.call_count, 1)

    def test_capture_recovers_after_previous_capture(self):
        order = self.create()
        error = urllib.error.HTTPError("https://example.com", 422, "already captured", {}, None)
        with patch.object(app, "paypal_request", side_effect=[error, self.completed()]) as paypal:
            self.assertEqual(app.capture_order(order), {"status": "paid"})
            self.assertEqual(paypal.call_args.args[0], "GET")

    def test_http_search_and_origin_check(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/api/products?q=NOTEBOOK") as response:
            products = json.load(response)
        self.assertEqual([p["name"] for p in products], ["NJIT Notebook"])
        request = urllib.request.Request(base + "/api/orders", data=b"{}", headers={
            "Content-Type": "application/json", "Origin": "https://untrusted.example",
        })
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        self.assertEqual(error.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
