"""Dependency-free smoke tests for constrained build environments."""

from azure_msp.evaluator import evaluate, kpis, proposals
from azure_msp.models import Resource


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
    print("smoke tests: passed")


if __name__ == "__main__":
    main()

