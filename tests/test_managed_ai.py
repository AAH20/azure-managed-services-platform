import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from azure_msp.managed_ai import (
    ServiceError,
    monthly_report,
    probe,
    record,
    render_html,
    validate_service,
)


class Endpoint(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert payload["stream"] is True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"ready"}}]}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_args):
        pass


def service(url):
    return {
        "service_id": "customer-ai", "customer": "Example Co", "base_url": url,
        "model": "example/model", "owner": "AI operations",
        "minimum_samples_per_month": 1,
        "minimum_sample_success_pct": 99.0,
        "maximum_p95_latency_ms": 2000,
        "maximum_p95_ttft_ms": 1000,
    }


class ManagedAITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.endpoint = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_live_probe_and_monthly_report(self):
        config = service(self.endpoint)
        sample = probe(config)
        self.assertTrue(sample["ok"])
        self.assertIsNotNone(sample["ttft_ms"])
        with TemporaryDirectory() as directory:
            db = Path(directory) / "observations.sqlite"
            record(db, sample)
            report = monthly_report(db, config, sample["observed_at"][:7])
            self.assertEqual(report["samples"], 1)
            self.assertEqual(report["status"], "met")
            self.assertIn("Example Co", render_html(report))
            self.assertIn("continuous availability", report["boundary"])

    def test_failure_is_recorded_and_never_passes(self):
        config = service("http://127.0.0.1:1")
        sample = probe(config, timeout_s=0.2)
        self.assertFalse(sample["ok"])
        with TemporaryDirectory() as directory:
            db = Path(directory) / "observations.sqlite"
            record(db, sample)
            report = monthly_report(db, config, sample["observed_at"][:7])
            self.assertEqual(report["status"], "attention")
            self.assertEqual(report["sample_success_pct"], 0)

    def test_no_samples_are_unknown(self):
        with TemporaryDirectory() as directory:
            report = monthly_report(Path(directory) / "missing.sqlite",
                                    service("https://example.com"), "2026-09")
            self.assertEqual(report["status"], "unknown")
            self.assertIsNone(report["sample_success_pct"])

    def test_too_few_samples_are_not_a_monthly_pass(self):
        config = {**service(self.endpoint), "minimum_samples_per_month": 10}
        sample = probe(config)
        with TemporaryDirectory() as directory:
            db = Path(directory) / "observations.sqlite"
            record(db, sample)
            report = monthly_report(db, config, sample["observed_at"][:7])
            self.assertEqual(report["status"], "insufficient_data")
            self.assertFalse(report["gates"]["sample_coverage"])

    def test_non_loopback_plaintext_endpoint_rejected(self):
        with self.assertRaisesRegex(ServiceError, "HTTPS"):
            validate_service(service("http://example.com"))
        with self.assertRaisesRegex(ServiceError, "base_url"):
            validate_service(service("https://user:pass@example.com"))


if __name__ == "__main__":
    unittest.main()
