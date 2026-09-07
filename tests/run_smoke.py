"""Dependency-free smoke tests for constrained build environments."""

import json
import subprocess

from azure_msp.azure_evidence import collect_baseline
from azure_msp.evaluator import evaluate, kpis, proposals
from azure_msp.models import Customer, Resource
from azure_msp.operations import TenantBoundaryError, build_work_queue, build_workloads, transition
from azure_msp.recovery import DrillEvent, RecoveryContract, evaluate_drill, recovery_order


def main() -> None:
    resource = Resource(
        id="/subscriptions/test/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
        name="vm1",
        type="microsoft.compute/virtualmachines",
        location="eastus",
        tags={"owner": "ops", "environment": "prod", "costCenter": "100"},
        properties={
            "monitoringEnabled": False,
            "backupEnabled": True,
            "daysSincePatchAssessment": 2,
        },
    )
    findings = evaluate([resource])
    assert [item.control_id for item in findings] == ["AZ-MSP-002"]
    assert proposals(findings)[0].approval_required is True
    assert kpis([resource], findings)["automatic_changes_executed"] == 0
    customer = Customer("test", "Test", "tenant-test")
    workloads = build_workloads(
        customer,
        [resource],
        [
            {
                "workload_id": "unassigned",
                "name": "Test workload",
                "owner": "ops",
                "criticality": "high",
                "monthly_revenue_usd": 100000,
            }
        ],
    )
    queue = build_work_queue(customer, workloads, findings, proposals(findings))
    event = transition(
        queue[0],
        authenticated_customer_id="test",
        action="approve",
        actor="owner@example.invalid",
        reason="Validated maintenance window and rollback plan",
    )
    assert event.new_status == "approved"
    try:
        transition(
            queue[0],
            authenticated_customer_id="other-customer",
            action="start",
            actor="intruder@example.invalid",
            reason="cross-tenant attempt",
        )
    except TenantBoundaryError:
        pass
    else:
        raise AssertionError("cross-tenant transition was not denied")

    class FakeRunner:
        def run(self, arguments):
            return subprocess.CompletedProcess(arguments, 0, json.dumps([]), "")

    observations = collect_baseline("tenant-test", "subscription-test", FakeRunner())
    assert len(observations) == 5
    assert all(item.status == "observed" for item in observations)
    assert all(item.receipt.raw_sha256 for item in observations)

    recovery_contract = RecoveryContract.from_dict(
        {
            "customer_id": "test",
            "workload_id": "service",
            "monthly_revenue_usd": 10000,
            "objectives": {"rto_minutes": 10, "rpo_minutes": 5},
            "approval": {
                "isolated_network_required": True,
                "production_failover_allowed": False,
                "required_approvers": ["owner"],
            },
            "components": [
                {
                    "component_id": "database",
                    "kind": "data",
                    "depends_on": [],
                    "validation": ["consistency"],
                }
            ],
        }
    )
    assert recovery_order(recovery_contract) == ["database"]
    recovery_report = evaluate_drill(
        recovery_contract,
        [DrillEvent("database", 0, 4, 2, {"consistency": True}, 0.5)],
    )
    assert recovery_report["outcome"] == "passed"
    assert recovery_report["cloud_mutations_executed"] == 0
    print("smoke tests: passed")


if __name__ == "__main__":
    main()
