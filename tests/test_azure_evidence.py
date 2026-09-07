import json
import subprocess

from azure_msp.azure_evidence import collect, collect_baseline


class FakeRunner:
    def __init__(self, returncode=0, stdout="[]", stderr=""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.calls = []

    def run(self, arguments):
        self.calls.append(arguments)
        return self.result


def test_successful_observation_has_provenance():
    runner = FakeRunner(stdout=json.dumps([{"id": "/subscriptions/sub/resource"}]))
    observation = collect(
        runner,
        source="resource-graph",
        tenant_id="tenant",
        subscription_id="sub",
        query_id="resources-v1",
        arguments=["graph", "query", "--query", "Resources"],
    )
    assert observation.status == "observed"
    assert observation.receipt.records == 1
    assert len(observation.receipt.raw_sha256) == 64
    assert runner.calls[0][-4:] == ["--subscription", "sub", "--output", "json"]


def test_cli_failure_is_unknown_not_compliant():
    observation = collect(
        FakeRunner(returncode=1, stderr="Forbidden"),
        source="policy-insights",
        tenant_id="tenant",
        subscription_id="sub",
        query_id="policy-state-v1",
        arguments=["policy", "state", "list"],
    )
    assert observation.status == "unknown"
    assert observation.receipt.error_class == "azure_cli_error"


def test_baseline_attempts_five_read_sources():
    runner = FakeRunner()
    observations = collect_baseline("tenant", "sub", runner)
    assert len(observations) == 5
    assert {item.source for item in observations} == {
        "resource-graph",
        "policy-insights",
        "advisor",
        "resource-health",
        "role-assignments",
    }
    assert all("delete" not in call and "create" not in call and "update" not in call for call in runner.calls)
