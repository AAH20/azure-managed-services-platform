import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from azure_msp.inference_fleet import FleetError, deployment_plan, evaluate_release
from azure_msp.inference_fleet_cli import main
from azure_msp.partner_cloud import create_order, get_order, register_partner, validate_catalog

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class InferenceFleetTests(unittest.TestCase):
    def setUp(self):
        self.order = {"order_id": "ord-123abc", "state": "requested", "model": "Qwen/Qwen3-0.6B"}
        self.spec = {"namespace": "pilot", "cache_pvc": "model-cache",
                     "hf_token_secret": "hf-read-token", "image": "vllm/vllm-openai@sha256:" + "a" * 64,
                     "model_revision": "b" * 40, "max_model_len": 2048, "gpu_count": 1}

    def test_plan_is_pinned_namespaced_and_order_bound(self):
        plan = deployment_plan(self.order, self.spec)
        deployment, service = plan["items"]
        self.assertEqual(deployment["metadata"]["namespace"], "pilot")
        self.assertEqual(service["spec"]["type"], "ClusterIP")
        self.assertEqual(deployment["spec"]["template"]["spec"]["containers"][0]["args"][1],
                         self.order["model"])
        self.assertEqual(deployment["spec"]["template"]["spec"]["containers"][0][
            "resources"]["limits"]["nvidia.com/gpu"], "1")
        with self.assertRaisesRegex(FleetError, "sha256"):
            deployment_plan(self.order, {**self.spec, "image": "vllm/vllm-openai:latest"})
        with self.assertRaisesRegex(FleetError, "commit SHA"):
            deployment_plan(self.order, {**self.spec, "model_revision": "main"})

    def test_synthetic_benchmark_requires_rollback(self):
        evidence = json.loads((EXAMPLES / "inference-fleet-benchmark.synthetic.json").read_text())
        result = evaluate_release(evidence)
        self.assertEqual(result["decision"], "rollback_required")
        self.assertEqual(result["baseline_cost_per_million_output_tokens_usd"], "200.00")
        self.assertEqual(result["candidate_cost_per_million_output_tokens_usd"], "240.00")
        self.assertIn("success", result["failed_gates"])
        self.assertIn("cost", result["failed_gates"])
        self.assertEqual(result["cluster_mutations_executed"], 0)

    def test_passing_candidate_is_review_only(self):
        evidence = json.loads((EXAMPLES / "inference-fleet-benchmark.synthetic.json").read_text())
        evidence["candidate"].update({"success_pct": 99.8, "p95_latency_ms": 1000,
                                      "p95_ttft_ms": 450, "gpu_hours": 0.4})
        result = evaluate_release(evidence)
        self.assertEqual(result["decision"], "eligible_for_operator_review")
        self.assertFalse(result["failed_gates"])

    def test_invalid_sample_or_cost_is_rejected(self):
        evidence = json.loads((EXAMPLES / "inference-fleet-benchmark.synthetic.json").read_text())
        evidence["candidate"]["output_tokens"] = 0
        with self.assertRaisesRegex(FleetError, "output_tokens"):
            evaluate_release(evidence)

    def test_mismatched_workload_is_rejected(self):
        evidence = json.loads((EXAMPLES / "inference-fleet-benchmark.synthetic.json").read_text())
        evidence["candidate"]["workload_sha256"] = "b" * 64
        with self.assertRaisesRegex(FleetError, "share workload"):
            evaluate_release(evidence)

    def test_plan_from_real_order_and_apply_requires_acknowledgement(self):
        catalog = validate_catalog(json.loads((EXAMPLES / "private-ai-partner-sku.json").read_text()))
        with TemporaryDirectory() as temp:
            db = Path(temp) / "orders.sqlite"
            output = Path(temp) / "plan.json"
            spec = Path(temp) / "spec.json"
            spec.write_text(json.dumps(self.spec))
            register_partner(db, "partner-a", "a" * 40)
            order = create_order(db, catalog, "partner-a", {
                "customer_id": "pilot-customer", "sku": catalog["sku"],
                "region": "customer-private-cloud"})
            self.assertEqual(get_order(db, "partner-a", order["order_id"])["state"], "requested")
            argv = ["fleet", "plan", "--db", str(db), "--partner-id", "partner-a",
                    "--order-id", order["order_id"], "--spec", str(spec), "--output", str(output)]
            with patch.object(sys, "argv", argv):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.read_text())["items"][0]["kind"], "Deployment")
            argv = ["fleet", "apply", "--plan", str(output), "--namespace", "pilot",
                    "--context", "test-context"]
            with patch.object(sys, "argv", argv), patch("subprocess.run") as run:
                with self.assertRaises(SystemExit):
                    main()
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
