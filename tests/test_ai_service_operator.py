import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from azure_msp.ai_service_operator import (
    AIServiceError,
    evaluate,
    partner_scores,
    plan_service,
    record_score,
)
from azure_msp.partner_cloud import create_order, register_partner, validate_catalog

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def fixture(name):
    return json.loads((EXAMPLES / name).read_text())


class AIServiceOperatorTests(unittest.TestCase):
    def setUp(self):
        self.contract = fixture("ai-service-contract.synthetic.json")
        self.evidence = fixture("ai-service-benchmark.synthetic.json")

    def test_paired_economics_and_release_gates(self):
        report = evaluate(self.contract, self.evidence)
        self.assertEqual(report["decision"], "eligible_for_operator_review")
        self.assertEqual(report["baseline"]["cost_per_accepted_usd"], "0.1800")
        self.assertEqual(report["candidate"]["total_test_window_cost_usd"], "0.1500")
        self.assertEqual(report["candidate"]["cost_per_accepted_usd"], "0.0500")
        self.assertEqual(report["candidate"]["accepted_results"], 3)

    def test_noncomparable_and_unsubstantiated_inputs_fail_closed(self):
        mismatch = deepcopy(self.evidence)
        mismatch["candidate"]["events"][0]["scenario_id"] = "other"
        with self.assertRaisesRegex(AIServiceError, "same scenario"):
            evaluate(self.contract, mismatch)
        missing_ref = deepcopy(self.evidence)
        missing_ref["candidate"]["events"][0]["acceptance_ref"] = None
        with self.assertRaisesRegex(AIServiceError, "acceptance evidence"):
            evaluate(self.contract, missing_ref)
        private_data = deepcopy(self.evidence)
        private_data["candidate"]["events"][0]["prompt"] = "do not ingest this"
        with self.assertRaisesRegex(AIServiceError, "allowed metric fields"):
            evaluate(self.contract, private_data)
        wrong_digest = deepcopy(self.evidence)
        wrong_digest["candidate"]["workload_sha256"] = "b" * 64
        with self.assertRaisesRegex(AIServiceError, "workload digest"):
            evaluate(self.contract, wrong_digest)

    def test_zero_accepted_and_quality_regression_hold(self):
        no_accepts = deepcopy(self.evidence)
        for event in no_accepts["candidate"]["events"]:
            event["accepted"] = False
            event["acceptance_ref"] = None
        report = evaluate(self.contract, no_accepts)
        self.assertEqual(report["decision"], "hold")
        self.assertIsNone(report["candidate"]["cost_per_accepted_usd"])
        self.assertIn("acceptance", report["failed_gates"])
        just_over_cap = deepcopy(self.evidence)
        just_over_cap["candidate"]["gpu_hours"] = "0.10012"
        precise = evaluate(self.contract, just_over_cap)
        self.assertEqual(precise["candidate"]["cost_per_accepted_usd"], "0.0500")
        lower_cap = deepcopy(self.contract)
        lower_cap["targets"]["maximum_cost_per_accepted_usd"] = "0.05"
        precise = evaluate(lower_cap, just_over_cap)
        self.assertIn("cost", precise["failed_gates"])

    def test_partner_order_scoping_and_idempotent_recording(self):
        with TemporaryDirectory() as temp:
            db = Path(temp) / "partner.sqlite"
            register_partner(db, "partner-a", "a" * 40)
            register_partner(db, "partner-b", "b" * 40)
            catalog = validate_catalog(fixture("private-ai-partner-sku.json"))
            order = create_order(db, catalog, "partner-a", {
                "customer_id": "customer-a", "sku": catalog["sku"],
                "region": "customer-private-cloud"})
            self.contract["order_id"] = order["order_id"]
            plan = plan_service(db, "partner-a", order["order_id"], {
                "namespace": "customer-inference", "cache_pvc": "model-cache",
                "hf_token_secret": "hf-read-token", "image": "vllm/image@sha256:" + "a" * 64,
                "model_revision": "b" * 40, "max_model_len": 2048, "gpu_count": 1})
            self.assertEqual([item["kind"] for item in plan["items"]], ["Deployment", "Service"])
            report = evaluate(self.contract, self.evidence)
            record_score(db, report)
            record_score(db, report)
            wrong_model = deepcopy(report)
            wrong_model["candidate"]["model"] = "different-model"
            with self.assertRaisesRegex(AIServiceError, "candidate model"):
                record_score(db, wrong_model)
            self.assertEqual(len(partner_scores(db, "partner-a", "a" * 40, order["order_id"])), 1)
            with self.assertRaisesRegex(AIServiceError, "authentication"):
                partner_scores(db, "partner-b", "wrong", order["order_id"])
            with self.assertRaisesRegex(AIServiceError, "order not found"):
                partner_scores(db, "partner-b", "b" * 40, order["order_id"])


if __name__ == "__main__":
    unittest.main()
