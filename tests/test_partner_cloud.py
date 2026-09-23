import json
import threading
import unittest
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib import error, request

from azure_msp.partner_cloud import (
    PartnerError,
    activate_order,
    create_order,
    get_order,
    register_partner,
    statement,
    validate_catalog,
)
from azure_msp.partner_cloud_cli import PartnerHandler

CATALOG = validate_catalog(json.loads(
    (Path(__file__).resolve().parents[1] / "examples/private-ai-partner-sku.json").read_text()
))


class PartnerCloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "partner.sqlite"
        register_partner(self.db, "partner-a", "a" * 40)
        register_partner(self.db, "partner-b", "b" * 40)

    def tearDown(self):
        self.temp.cleanup()

    def test_order_price_snapshot_activation_and_draft_statement(self):
        order = create_order(self.db, CATALOG, "partner-a", {
            "customer_id": "customer-a", "sku": CATALOG["sku"],
            "region": "customer-private-cloud"})
        self.assertEqual(order["state"], "requested")
        self.assertEqual(order["prices_usd"]["retail_monthly_usd"], "2500.00")
        self.assertIsNone(get_order(self.db, "partner-b", order["order_id"]))
        month = datetime.now(UTC).strftime("%Y-%m")
        self.assertEqual(statement(self.db, CATALOG, "partner-a", order["order_id"], month)[
            "draft_customer_charge_usd"], "0.00")
        active = activate_order(self.db, order["order_id"], "cluster-deployment-1",
                                "signed-contract-1", "https://ai.example.invalid")
        self.assertEqual(active["state"], "active")
        draft = statement(self.db, CATALOG, "partner-a", order["order_id"], month)
        self.assertEqual(draft["draft_customer_charge_usd"], "5500.00")
        self.assertEqual(draft["draft_partner_wholesale_usd"], "4400.00")
        self.assertEqual(draft["service_probe_report"]["status"], "unknown")
        self.assertIn("no invoice", draft["boundary"])
        with self.assertRaisesRegex(PartnerError, "not awaiting activation"):
            activate_order(self.db, order["order_id"], "again", "again", "https://ai.example.invalid")

    def test_server_auth_and_partner_isolation(self):
        handler = type("TestPartnerHandler", (PartnerHandler,), {"db": self.db, "catalog": CATALOG})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(error.HTTPError) as denied:
                request.urlopen(base + "/v1/orders")
            self.assertEqual(denied.exception.code, 401)
            payload = json.dumps({"customer_id": "customer-a", "sku": CATALOG["sku"],
                                  "region": "customer-private-cloud"}).encode()
            headers = {"X-Partner-ID": "partner-a", "Authorization": "Bearer " + "a" * 40,
                       "Content-Type": "application/json"}
            created = request.Request(base + "/v1/orders", payload, headers, method="POST")
            with request.urlopen(created) as response:
                order = json.load(response)
            self.assertEqual(order["state"], "requested")
            other = request.Request(base + "/v1/orders/" + order["order_id"], headers={
                "X-Partner-ID": "partner-b", "Authorization": "Bearer " + "b" * 40})
            with self.assertRaises(error.HTTPError) as hidden:
                request.urlopen(other)
            self.assertEqual(hidden.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_unapproved_region_and_bad_endpoint_rejected(self):
        with self.assertRaisesRegex(PartnerError, "region"):
            create_order(self.db, CATALOG, "partner-a", {
                "customer_id": "customer-a", "sku": CATALOG["sku"], "region": "other"})
        order = create_order(self.db, CATALOG, "partner-a", {
            "customer_id": "customer-a", "sku": CATALOG["sku"],
            "region": "customer-private-cloud"})
        with self.assertRaisesRegex(PartnerError, "bare HTTPS"):
            activate_order(self.db, order["order_id"], "deploy", "contract",
                           "https://user:secret@example.com")


if __name__ == "__main__":
    unittest.main()
