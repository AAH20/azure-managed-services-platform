import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from azure_msp.partner_cloud import register_partner
from azure_msp.recovery_operator import (
    DrillError,
    evaluate,
    list_followups,
    probe_application,
    record_result,
    validate_contract,
)
from azure_msp.service_delivery import activate, customer_report, onboard, request_pilot

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return b'{"checkout":{"status":"ready"}}'


class FakeOpener:
    def open(self, request, timeout):
        assert request.full_url == "https://example.invalid/health"
        assert timeout == 10
        return FakeResponse()


class RecoveryOperatorTests(unittest.TestCase):
    def setUp(self):
        self.contract = fixture("recovery-operator-contract.synthetic.json")
        self.restore = fixture("recovery-operator-restore.synthetic.json")
        self.probe = fixture("recovery-operator-probe.synthetic.json")

    def test_complete_drill_and_threshold_failure(self):
        result = evaluate(self.contract, self.restore, self.probe)
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(result["actual_rto_seconds"], 1620)
        self.assertEqual(result["actual_rpo_seconds"], 900)
        self.contract["rto_minutes"] = 20
        failed = evaluate(self.contract, self.restore, self.probe)
        self.assertEqual(failed["outcome"], "failed")
        self.assertFalse(failed["checks"]["rto"])
        no_transaction = evaluate(self.contract, self.restore, {**self.probe, "passed": False})
        self.assertIsNone(no_transaction["actual_rto_seconds"])

    def test_isolation_scope_cleanup_and_probe_must_match(self):
        wrong = deepcopy(self.restore)
        wrong["isolated"] = False
        with self.assertRaisesRegex(DrillError, "isolated"):
            evaluate(self.contract, wrong, self.probe)
        wrong = deepcopy(self.restore)
        wrong["scope"]["vm_name"] = "other"
        with self.assertRaisesRegex(DrillError, "scope"):
            evaluate(self.contract, wrong, self.probe)
        wrong = deepcopy(self.restore)
        wrong["cleanup_confirmed"] = False
        with self.assertRaisesRegex(DrillError, "cleanup"):
            evaluate(self.contract, wrong, self.probe)
        bad_probe = {**self.probe, "probe_url": "https://other.invalid/health"}
        with self.assertRaisesRegex(DrillError, "probe result"):
            evaluate(self.contract, self.restore, bad_probe)

    def test_readonly_probe_and_url_boundary(self):
        with patch("azure_msp.recovery_operator.build_opener", return_value=FakeOpener()):
            result = probe_application(self.contract)
        self.assertTrue(result["passed"])
        self.assertEqual(result["http_status"], 200)
        bad = deepcopy(self.contract)
        bad["probe"]["url"] = "http://127.0.0.1/private"
        with self.assertRaisesRegex(DrillError, "HTTPS"):
            validate_contract(bad)
        bad["probe"]["url"] = "https://example.invalid/health?token=secret"
        with self.assertRaisesRegex(DrillError, "HTTPS"):
            validate_contract(bad)

    def test_pilot_result_visible_without_internal_economics(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / "pilot.sqlite"
            register_partner(db, "partner-a", "a" * 40)
            request = request_pilot(db, "partner-a", "a" * 40, "customer-a", 1)
            pilot = onboard(db, request["request_id"], self.contract["scope"], "signed-msa",
                            "1200.00", "90.00", "c" * 40)
            snapshot = fixture("backup-resolution-before.synthetic.json")
            snapshot["receipts"] = [{"status": "observed", "tenant_id": snapshot["scope"]["tenant_id"],
                                      "subscription_id": snapshot["scope"]["subscription_id"]}
                                     for _ in range(4)]
            activate(db, pilot["pilot_id"], snapshot)
            report = evaluate(self.contract, self.restore, self.probe)
            with self.assertRaisesRegex(DrillError, "predates pilot activation"):
                record_result(db, pilot["pilot_id"], report)
            # Replay a historical synthetic pilot without waiting for a real drill.
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE delivery_pilots SET active_at=? WHERE pilot_id=?",
                             ("2026-09-19T00:00:00+00:00", pilot["pilot_id"]))
            record_result(db, pilot["pilot_id"], report)
            record_result(db, pilot["pilot_id"], report)
            customer = customer_report(db, pilot["pilot_id"], "c" * 40, "2026-09")
            self.assertEqual(len(customer["recovery_drills"]), 1)
            self.assertEqual(customer["recovery_drills"][0]["outcome"], "passed")
            self.assertNotIn("estimated_operator_labor_usd", customer)
            failed = evaluate(self.contract, self.restore, {**self.probe, "passed": False})
            record_result(db, pilot["pilot_id"], failed)
            record_result(db, pilot["pilot_id"], failed)
            self.assertEqual(len(list_followups(db, pilot["pilot_id"])), 1)
            self.assertEqual(list_followups(db, pilot["pilot_id"])[0]["state"], "open")


if __name__ == "__main__":
    unittest.main()
