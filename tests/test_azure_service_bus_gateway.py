import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from azure_msp.azure_service_bus_gateway import (
    InvalidTransportMessage,
    open_delivered_case,
    prepare_alert,
    settle_one,
    validate_address,
)
from azure_msp.customer_gateway import GatewayError
from azure_msp.resolution_network import get_case

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class FakeReceiver:
    def __init__(self):
        self.calls = []

    def complete_message(self, message):
        self.calls.append("complete")

    def dead_letter_message(self, message, *, reason):
        self.calls.append(("dead_letter", reason))

    def abandon_message(self, message):
        self.calls.append("abandon")


class ServiceBusGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.db = Path(self.temp.name) / "case.sqlite"
        self.config = json.loads((EXAMPLES / "customer-gateway.synthetic.json").read_text())
        self.alert = json.loads((EXAMPLES / "backup-alert.synthetic.json").read_text())
        self.body = json.dumps(self.alert, sort_keys=True).encode()
        _, self.message_id = prepare_alert(self.config, self.body)

    def tearDown(self):
        self.temp.cleanup()

    def test_settles_replayed_alert_into_one_case(self):
        receiver = FakeReceiver()
        msg = SimpleNamespace(body=[self.body], message_id=self.message_id)
        first = settle_one(receiver, msg, self.db, self.config)
        second = settle_one(receiver, msg, self.db, self.config)
        self.assertEqual(first["case_id"], second["case_id"])
        self.assertEqual(get_case(self.db, first["case_id"])["state"], "received")
        self.assertEqual(receiver.calls, ["complete", "complete"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 1)

    def test_conflicting_payload_and_cross_customer_rejected(self):
        open_delivered_case(self.db, self.config, self.body, self.message_id)
        altered = deepcopy(self.alert)
        altered["data"]["essentials"]["severity"] = "Sev1"
        with self.assertRaisesRegex(InvalidTransportMessage, "conflicting"):
            open_delivered_case(self.db, self.config, json.dumps(altered).encode(), self.message_id)
        other = deepcopy(self.config)
        other["customer_id"] = "customer-b"
        with self.assertRaisesRegex(GatewayError, "different customer"):
            open_delivered_case(self.db, other, self.body,
                                prepare_alert(other, self.body)[1])

    def test_bad_delivery_deadletters_and_worker_failure_abandons(self):
        receiver = FakeReceiver()
        wrong_id = SimpleNamespace(body=self.body, message_id="wrong")
        self.assertEqual(settle_one(receiver, wrong_id, self.db, self.config)["state"],
                         "dead_lettered")
        self.assertEqual(receiver.calls, [("dead_letter", "InvalidScopedAlert")])
        wrong_scope = deepcopy(self.alert)
        wrong_scope["data"]["essentials"]["alertTargetIDs"] = ["/subscriptions/other"]
        out_of_scope = SimpleNamespace(body=json.dumps(wrong_scope).encode(),
                                       message_id=self.message_id)
        settle_one(receiver, out_of_scope, self.db, self.config)
        self.assertEqual(receiver.calls[-1], ("dead_letter", "InvalidScopedAlert"))
        valid = SimpleNamespace(body=self.body, message_id=self.message_id)
        with (patch("azure_msp.azure_service_bus_gateway.open_case", side_effect=OSError("disk")),
              self.assertRaises(OSError)):
            settle_one(receiver, valid, self.db, self.config)
        self.assertEqual(receiver.calls[-1], "abandon")
        result = settle_one(receiver, valid, self.db, self.config)
        self.assertEqual(result["state"], "received")
        self.assertEqual(receiver.calls[-1], "complete")

    def test_crash_after_case_creation_is_idempotent(self):
        real_open = __import__("azure_msp.production_resolution", fromlist=["open_case"]).open_case

        def crash_after_case(db, recipe, alert, scope):
            real_open(db, recipe, alert, scope)
            raise OSError("crash before broker settlement")

        receiver = FakeReceiver()
        msg = SimpleNamespace(body=self.body, message_id=self.message_id)
        with (patch("azure_msp.azure_service_bus_gateway.open_case", side_effect=crash_after_case),
              self.assertRaises(OSError)):
            settle_one(receiver, msg, self.db, self.config)
        self.assertEqual(receiver.calls, ["abandon"])
        result = settle_one(receiver, msg, self.db, self.config)
        self.assertEqual(receiver.calls, ["abandon", "complete"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT case_id FROM bus_alerts").fetchone()[0],
                             result["case_id"])

    def test_address_validation(self):
        self.assertEqual(validate_address("pilot-bus.servicebus.windows.net", "alerts"),
                         ("pilot-bus.servicebus.windows.net", "alerts"))
        with self.assertRaises(ValueError):
            validate_address("Endpoint=sb://pilot-bus.servicebus.windows.net/", "alerts")


if __name__ == "__main__":
    unittest.main()
