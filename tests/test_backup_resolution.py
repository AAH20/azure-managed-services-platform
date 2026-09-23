import json
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path

from azure_msp.backup_resolution import (
    BackupResolutionError,
    collect_snapshot,
    propose,
    retry_backup,
    verify,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def run(self, arguments):
        self.calls.append(arguments)
        return self.responses[len(self.calls) - 1]


class BackupResolutionTests(unittest.TestCase):
    def setUp(self):
        self.before = fixture("backup-resolution-before.synthetic.json")
        self.after = fixture("backup-resolution-after.synthetic.json")
        self.attempt = fixture("backup-resolution-attempt.synthetic.json")

    def test_failed_job_proposes_reviewed_retry_and_verifies_new_point(self):
        plan = propose(self.before)
        self.assertEqual(plan["action"], "backup_now_candidate")
        self.assertEqual(plan["failed_job_id"], "old-failed-job")
        outcome = verify(self.before, self.after, self.attempt)
        self.assertEqual(outcome["status"], "verified_backup")
        self.assertFalse(outcome["restorability_proven"])

    def test_no_new_point_cannot_be_called_resolved(self):
        after = deepcopy(self.after)
        after["recovery_points"] = self.before["recovery_points"]
        self.assertEqual(verify(self.before, after, self.attempt)["status"], "unverified")
        after["source_status"] = "unknown"
        self.assertEqual(verify(self.before, after, self.attempt)["status"], "unknown")
        attempt = deepcopy(self.attempt)
        attempt["request_job_id"] = None
        self.assertEqual(verify(self.before, self.after, attempt)["status"], "unknown")

    def test_retry_requires_both_approvers_and_unchanged_failed_job(self):
        plan = propose(self.before)
        runner = FakeRunner([subprocess.CompletedProcess([], 0, '{"name":"new-completed-job"}', "")])
        with self.assertRaisesRegex(BackupResolutionError, "missing required approvals"):
            retry_backup(self.before["scope"], plan, self.before, {"backup-operator"}, runner)
        self.assertFalse(runner.calls)
        current = deepcopy(self.before)
        current["jobs"][0]["job_id"] = "different-failed-job"
        with self.assertRaisesRegex(BackupResolutionError, "state changed"):
            retry_backup(self.before["scope"], plan, current,
                         {"backup-operator", "workload-owner"}, runner)
        self.assertFalse(runner.calls)
        current["account_verified"] = False
        with self.assertRaisesRegex(BackupResolutionError, "account scope"):
            retry_backup(self.before["scope"], plan, current,
                         {"backup-operator", "workload-owner"}, runner)
        self.assertFalse(runner.calls)
        result = retry_backup(self.before["scope"], plan, self.before,
                              {"backup-operator", "workload-owner"}, runner)
        self.assertEqual(result["request_status"], "accepted")
        self.assertEqual(result["request_job_id"], "new-completed-job")
        self.assertEqual(runner.calls[0][:3], ["backup", "protection", "backup-now"])
        self.assertIn(self.before["scope"]["subscription_id"], runner.calls[0])

    def test_collector_scopes_four_read_calls_and_unknown_stays_unknown(self):
        responses = [
            subprocess.CompletedProcess([], 0, json.dumps({
                "id": self.before["scope"]["subscription_id"],
                "tenantId": self.before["scope"]["tenant_id"]}), ""),
            subprocess.CompletedProcess([], 0, json.dumps([{
                "name": "old-failed-job", "properties": {"entityFriendlyName": "pilot-vm",
                    "operation": "Backup", "status": "Failed",
                    "startTime": "2026-09-20T10:00:00Z"}}]), ""),
            subprocess.CompletedProcess([], 0, json.dumps({"name": "pilot-vm"}), ""),
            subprocess.CompletedProcess([], 0, json.dumps([{
                "name": "old-point", "properties": {
                    "recoveryPointTime": "2026-09-19T10:00:00Z"}}]), ""),
        ]
        runner = FakeRunner(responses)
        snapshot = collect_snapshot(self.before["scope"], runner)
        self.assertEqual(snapshot["source_status"], "observed")
        self.assertEqual(snapshot["jobs"][0]["job_id"], "old-failed-job")
        self.assertEqual(len(runner.calls), 4)
        self.assertTrue(all("--subscription" in call for call in runner.calls))
        responses[1] = subprocess.CompletedProcess([], 1, "", "permission denied")
        unknown = collect_snapshot(self.before["scope"], FakeRunner(responses))
        self.assertEqual(unknown["source_status"], "unknown")
        self.assertEqual(propose(unknown)["action"], "manual_review")
        responses[0] = subprocess.CompletedProcess([], 0, json.dumps({
            "id": self.before["scope"]["subscription_id"], "tenantId": "wrong-tenant"}), "")
        mismatch = collect_snapshot(self.before["scope"], FakeRunner(responses))
        self.assertFalse(mismatch["account_verified"])


if __name__ == "__main__":
    unittest.main()
