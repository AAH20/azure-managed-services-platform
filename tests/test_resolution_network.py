import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from azure_msp.resolution_network import (
    NetworkError,
    diagnose,
    ingest,
    record_attempt,
    record_verification,
    verify_chain,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class ResolutionNetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "cases.sqlite"
        self.recipe = fixture("workcells/azure-vm-backup-resolution.json")
        self.alert = fixture("backup-alert.synthetic.json")
        self.before = fixture("backup-resolution-before.synthetic.json")
        self.after = fixture("backup-resolution-after.synthetic.json")
        self.attempt = fixture("backup-resolution-attempt.synthetic.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_full_case_and_duplicate_alert_idempotency(self):
        case = ingest(self.db, self.recipe, self.alert, self.before["scope"])
        same = ingest(self.db, self.recipe, self.alert, self.before["scope"])
        self.assertEqual(case["case_id"], same["case_id"])
        self.assertEqual(len(same["events"]), 1)
        case = diagnose(self.db, case["case_id"], self.before)
        self.assertEqual(case["state"], "diagnosed")
        case = record_attempt(self.db, case["case_id"], self.attempt)
        self.assertEqual(case["state"], "awaiting_result")
        case = record_verification(self.db, case["case_id"], self.after)
        self.assertEqual(case["state"], "verified")
        self.assertEqual(case["outcome"]["status"], "verified_backup")
        self.assertEqual(len(case["events"]), 4)
        self.assertTrue(verify_chain(self.db, case["case_id"]))
        same = record_verification(self.db, case["case_id"], self.after)
        self.assertEqual(len(same["events"]), 4)

    def test_alert_scope_and_attempt_authority_fail_closed(self):
        wrong = deepcopy(self.alert)
        wrong["data"]["essentials"]["alertTargetIDs"] = [
            "/subscriptions/another/resourceGroups/x/providers/Microsoft.RecoveryServices/vaults/x"]
        with self.assertRaisesRegex(NetworkError, "outside"):
            ingest(self.db, self.recipe, wrong, self.before["scope"])
        case = ingest(self.db, self.recipe, self.alert, self.before["scope"])
        with self.assertRaisesRegex(NetworkError, "awaiting"):
            record_attempt(self.db, case["case_id"], self.attempt)
        diagnose(self.db, case["case_id"], self.before)
        bad = {**self.attempt, "declared_approvals": ["backup-operator"]}
        with self.assertRaisesRegex(NetworkError, "both declared approvals"):
            record_attempt(self.db, case["case_id"], bad)

    def test_unverified_result_can_be_rechecked(self):
        case = ingest(self.db, self.recipe, self.alert, self.before["scope"])
        diagnose(self.db, case["case_id"], self.before)
        record_attempt(self.db, case["case_id"], self.attempt)
        incomplete = deepcopy(self.after)
        incomplete["recovery_points"] = self.before["recovery_points"]
        pending = record_verification(self.db, case["case_id"], incomplete)
        self.assertEqual(pending["state"], "awaiting_result")
        complete = record_verification(self.db, case["case_id"], self.after)
        self.assertEqual(complete["state"], "verified")

    def test_event_chain_detects_accidental_tampering(self):
        case = ingest(self.db, self.recipe, self.alert, self.before["scope"])
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE case_events SET event_json='{}' WHERE case_id=?",
                         (case["case_id"],))
        self.assertFalse(verify_chain(self.db, case["case_id"]))


if __name__ == "__main__":
    unittest.main()
