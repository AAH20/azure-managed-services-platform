import json
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from azure_msp.partner_cloud import register_partner
from azure_msp.resolution_network import ingest
from azure_msp.service_delivery import (
    DeliveryError,
    activate,
    customer_report,
    list_requests,
    onboard,
    operator_statement,
    record_work,
    request_pilot,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class ServiceDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "delivery.sqlite"
        register_partner(self.db, "partner-a", "a" * 40)
        register_partner(self.db, "partner-b", "b" * 40)
        self.snapshot = fixture("backup-resolution-before.synthetic.json")
        self.scope = self.snapshot["scope"]
        self.snapshot["receipts"] = [{"status": "observed", "tenant_id": self.scope["tenant_id"],
                                      "subscription_id": self.scope["subscription_id"],
                                      "source": source, "synthetic": True}
                                     for source in ("account", "jobs", "item", "points")]
        self.token = "c" * 40

    def tearDown(self):
        self.temp.cleanup()

    def _onboard(self):
        request = request_pilot(self.db, "partner-a", "a" * 40, "customer-a", 1)
        return onboard(self.db, request["request_id"], self.scope, "signed-msa-1",
                       "1200.00", "90.00", self.token)

    def test_intake_isolation_and_contract_gate(self):
        with self.assertRaisesRegex(DeliveryError, "authentication"):
            request_pilot(self.db, "partner-a", "wrong", "customer-a", 1)
        pilot = self._onboard()
        self.assertEqual(list_requests(self.db, "partner-b", "b" * 40), [])
        self.assertNotIn("customer_token_hash", pilot)
        month = datetime.now(UTC).strftime("%Y-%m")
        self.assertEqual(customer_report(self.db, pilot["pilot_id"], self.token, month)[
            "draft_customer_charge_usd"], "0.00")
        with self.assertRaisesRegex(DeliveryError, "authentication"):
            customer_report(self.db, pilot["pilot_id"], "wrong", month)

    def test_activation_case_reporting_and_labor(self):
        pilot = self._onboard()
        bad = deepcopy(self.snapshot)
        bad["account_verified"] = False
        with self.assertRaisesRegex(DeliveryError, "observed"):
            activate(self.db, pilot["pilot_id"], bad)
        pilot = activate(self.db, pilot["pilot_id"], self.snapshot)
        self.assertEqual(pilot["state"], "active")
        recipe = fixture("workcells/azure-vm-backup-resolution.json")
        case = ingest(self.db, recipe, fixture("backup-alert.synthetic.json"), self.scope)
        record_work(self.db, pilot["pilot_id"], case["case_id"], 90, "Reviewed backup alert")
        month = datetime.now(UTC).strftime("%Y-%m")
        report = customer_report(self.db, pilot["pilot_id"], self.token, month)
        self.assertEqual(report["draft_customer_charge_usd"], "1200.00")
        self.assertNotIn("estimated_operator_labor_usd", report)
        statement = operator_statement(self.db, pilot["pilot_id"], month)
        self.assertEqual(statement["estimated_operator_labor_usd"], "135.00")
        self.assertEqual(statement["estimated_contribution_after_operator_labor_usd"], "1065.00")
        self.assertEqual(report["case_count"], 1)
        self.assertNotIn("snapshot", json.dumps(report))
        self.assertNotIn("alert_id", json.dumps(report))

    def test_out_of_scope_case_rejected(self):
        pilot = self._onboard()
        activate(self.db, pilot["pilot_id"], self.snapshot)
        other = dict(self.scope, vm_name="other-vm", incident_id="other-incident")
        alert = fixture("backup-alert.synthetic.json")
        alert["data"]["essentials"]["alertId"] = "other-alert"
        case = ingest(self.db, fixture("workcells/azure-vm-backup-resolution.json"), alert, other)
        with self.assertRaisesRegex(DeliveryError, "outside pilot scope"):
            record_work(self.db, pilot["pilot_id"], case["case_id"], 30, "Other VM")
        month = datetime.now(UTC).strftime("%Y-%m")
        self.assertEqual(customer_report(self.db, pilot["pilot_id"], self.token, month)["cases"], [])


if __name__ == "__main__":
    unittest.main()
