import json
import subprocess
import unittest
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from azure_msp.partner_cloud import register_partner
from azure_msp.production_resolution import (
    OperatorError,
    attach_drill,
    case_report,
    diagnose_case,
    execute_retry,
    open_case,
    verify_case,
)
from azure_msp.resolution_network import get_case
from azure_msp.service_delivery import activate, customer_report, onboard, request_pilot

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class FakeRunner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def run(self, args):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


class ProductionResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "operator.sqlite"
        self.before = fixture("backup-resolution-before.synthetic.json")
        self.after = fixture("backup-resolution-after.synthetic.json")
        self.recipe = fixture("workcells/azure-vm-backup-resolution.json")
        self.alert = fixture("backup-alert.synthetic.json")

    def tearDown(self):
        self.temp.cleanup()

    def _diagnosed(self):
        case = open_case(self.db, self.recipe, self.alert, self.before["scope"])
        diagnose_case(self.db, case["case_id"], snapshot=self.before)
        return case["case_id"]

    def test_full_backup_and_bound_restore_drill(self):
        case_id = self._diagnosed()
        runner = FakeRunner(subprocess.CompletedProcess([], 0, '{"name":"new-completed-job"}', ""))
        case = execute_retry(self.db, case_id, approvals={"backup-operator", "workload-owner"},
                             cause_reviewed=True, acknowledge_cloud_write=True,
                             current=self.before, runner=runner)
        self.assertEqual(case["state"], "awaiting_result")
        self.assertEqual(len(runner.calls), 1)
        started = case["attempt"]["started_at"]
        self.after["jobs"][1]["started_at"] = started
        self.after["recovery_points"][1]["created_at"] = started
        case = verify_case(self.db, case_id, after=self.after)
        self.assertEqual(case["state"], "verified")
        register_partner(self.db, "partner-a", "a" * 40)
        request = request_pilot(self.db, "partner-a", "a" * 40, "customer-a", 1)
        pilot = onboard(self.db, request["request_id"], self.before["scope"], "signed-msa",
                        "1200.00", "90.00", "c" * 40)
        snapshot = deepcopy(self.before)
        snapshot["receipts"] = [{"status": "observed", "tenant_id": snapshot["scope"]["tenant_id"],
                                  "subscription_id": snapshot["scope"]["subscription_id"]}
                                 for _ in range(4)]
        activate(self.db, pilot["pilot_id"], snapshot)
        contract = fixture("recovery-operator-contract.synthetic.json")
        contract["rpo_minutes"] = 10080
        restore = fixture("recovery-operator-restore.synthetic.json")
        restore["recovery_point_at"] = started
        now = datetime.now(UTC).isoformat()
        restore.update(started_at=now, restored_at=now, cleanup_at=now)
        probe = fixture("recovery-operator-probe.synthetic.json")
        probe["checked_at"] = now
        attached = attach_drill(self.db, case_id, contract, restore, probe,
                                pilot_id=pilot["pilot_id"])
        self.assertEqual(attached["outcome"], "passed")
        attach_drill(self.db, case_id, contract, restore, probe, pilot_id=pilot["pilot_id"])
        report = case_report(self.db, case_id)
        self.assertEqual(report["backup_result"], "verified_backup")
        self.assertEqual(len(report["drills"]), 1)
        self.assertEqual(report["drills"][0]["outcome"], "passed")
        self.assertTrue(report["event_chain_valid"])
        self.assertEqual(len(customer_report(self.db, pilot["pilot_id"], "c" * 40,
                                             datetime.now(UTC).strftime("%Y-%m"))["recovery_drills"]), 1)
        wrong = deepcopy(restore)
        wrong["recovery_point_at"] = "2026-09-19T10:00:00Z"
        with self.assertRaisesRegex(OperatorError, "newly verified recovery point"):
            attach_drill(self.db, case_id, contract, wrong, probe)

    def test_failed_write_is_uncertain_and_never_automatically_retried(self):
        case_id = self._diagnosed()
        runner = FakeRunner(error=TimeoutError("simulated Azure timeout"))
        with self.assertRaises(TimeoutError):
            execute_retry(self.db, case_id, approvals={"backup-operator", "workload-owner"},
                          cause_reviewed=True, acknowledge_cloud_write=True,
                          current=self.before, runner=runner)
        self.assertEqual(case_report(self.db, case_id)["write_intent"]["state"], "uncertain")
        with self.assertRaisesRegex(OperatorError, "write intent already exists"):
            execute_retry(self.db, case_id, approvals={"backup-operator", "workload-owner"},
                          cause_reviewed=True, acknowledge_cloud_write=True,
                          current=self.before, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(get_case(self.db, case_id)["state"], "diagnosed")

    def test_approval_and_freshness_gates_precede_write_intent(self):
        case_id = self._diagnosed()
        runner = FakeRunner(subprocess.CompletedProcess([], 0, '{"name":"job"}', ""))
        with self.assertRaisesRegex(OperatorError, "approvals"):
            execute_retry(self.db, case_id, approvals={"backup-operator"},
                          cause_reviewed=True, acknowledge_cloud_write=True,
                          current=self.before, runner=runner)
        stale = deepcopy(self.before)
        stale["jobs"][0]["status"] = "Completed"
        with self.assertRaisesRegex(OperatorError, "condition changed"):
            execute_retry(self.db, case_id, approvals={"backup-operator", "workload-owner"},
                          cause_reviewed=True, acknowledge_cloud_write=True,
                          current=stale, runner=runner)
        self.assertIsNone(case_report(self.db, case_id)["write_intent"])
        self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
