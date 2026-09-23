import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from azure_msp.customer_gateway import GatewayError, enqueue, queue_status, sign_payload, work_once
from azure_msp.customer_gateway_cli import serve
from azure_msp.resolution_network import get_case

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "gateway.sqlite"
        self.config = fixture("customer-gateway.synthetic.json")
        self.alert = fixture("backup-alert.synthetic.json")
        self.body = json.dumps(self.alert, sort_keys=True).encode()
        self.key = "high-entropy-development-key-000000000000"
        self.now = 1_800_000_000

    def tearDown(self):
        self.temp.cleanup()

    def _enqueue(self, body=None, nonce="nonce-unique-0001"):
        body = self.body if body is None else body
        return enqueue(self.db, self.config, body, timestamp=self.now, nonce=nonce,
                       signature=sign_payload(self.key, self.now, nonce, body),
                       key=self.key, now=self.now)

    def test_authenticated_deduplicated_alert_opens_one_case(self):
        first = self._enqueue()
        duplicate = self._enqueue(nonce="nonce-unique-0002")
        self.assertEqual(first["message_id"], duplicate["message_id"])
        self.assertTrue(duplicate["duplicate"])
        result = work_once(self.db, self.config, now=self.now)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(get_case(self.db, result["case_id"])["state"], "received")
        self.assertIsNone(work_once(self.db, self.config, now=self.now))
        self.assertEqual(queue_status(self.db, self.config)["counts"], {"complete": 1})

    def test_replay_bad_signature_and_scope_rejected(self):
        self._enqueue()
        with self.assertRaisesRegex(GatewayError, "replayed"):
            self._enqueue()
        with self.assertRaisesRegex(GatewayError, "signature"):
            enqueue(self.db, self.config, self.body, timestamp=self.now,
                    nonce="nonce-unique-0003", signature="0" * 64,
                    key=self.key, now=self.now)
        with self.assertRaisesRegex(GatewayError, "five-minute"):
            enqueue(self.db, self.config, self.body, timestamp=self.now - 301,
                    nonce="nonce-unique-0004", signature="0" * 64,
                    key=self.key, now=self.now)
        wrong = deepcopy(self.alert)
        wrong["data"]["essentials"]["alertTargetIDs"] = ["/subscriptions/other/resourceGroups/x"]
        with self.assertRaisesRegex(ValueError, "outside"):
            self._enqueue(json.dumps(wrong).encode(), nonce="nonce-unique-0005")
        conflict = deepcopy(self.alert)
        conflict["data"]["essentials"]["severity"] = "Sev1"
        with self.assertRaisesRegex(GatewayError, "conflicting"):
            self._enqueue(json.dumps(conflict).encode(), nonce="nonce-unique-0006")
        other_customer = deepcopy(self.config)
        other_customer["customer_id"] = "customer-b"
        with self.assertRaisesRegex(GatewayError, "different customer"):
            work_once(self.db, other_customer, now=self.now)

    def test_worker_failure_retries_without_duplicate_case(self):
        self._enqueue()
        with patch("azure_msp.customer_gateway.open_case", side_effect=RuntimeError("simulated crash")):
            failed = work_once(self.db, self.config, now=self.now)
        self.assertEqual(failed["state"], "pending")
        result = work_once(self.db, self.config, now=self.now)
        self.assertEqual(result["state"], "complete")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE gateway_messages SET state='leased',lease_until=?",
                         (self.now - 1,))
        again = work_once(self.db, self.config, now=self.now)
        self.assertEqual(again["case_id"], result["case_id"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 1)

    def test_builtin_http_server_rejects_public_bind(self):
        with self.assertRaisesRegex(GatewayError, "loopback"):
            serve(self.db, self.config, self.key, "0.0.0.0", 8173)


if __name__ == "__main__":
    unittest.main()
