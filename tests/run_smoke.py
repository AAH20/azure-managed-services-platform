"""Dependency-free smoke tests for constrained build environments."""

from azure_msp.evaluator import evaluate, kpis, proposals
from azure_msp.models import Customer, Resource
from azure_msp.operations import TenantBoundaryError, build_work_queue, build_workloads, transition


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
    print("smoke tests: passed")


if __name__ == "__main__":
    main()
