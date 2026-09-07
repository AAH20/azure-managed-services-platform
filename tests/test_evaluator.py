from azure_msp.evaluator import evaluate, kpis, proposals
from azure_msp.models import Resource


def vm(**overrides):
    properties = {
        "monitoringEnabled": True,
        "backupEnabled": True,
        "daysSincePatchAssessment": 2,
    }
    properties.update(overrides)
    return Resource(
        id="/subscriptions/test/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
        name="vm1",
        type="microsoft.compute/virtualmachines",
        location="eastus",
        tags={"owner": "ops", "environment": "prod", "costCenter": "100"},
        properties=properties,
    )


def test_compliant_vm_has_no_findings():
    assert evaluate([vm()]) == []


def test_missing_monitoring_generates_review_gated_proposal():
    findings = evaluate([vm(monitoringEnabled=False)])
    assert [item.control_id for item in findings] == ["AZ-MSP-002"]
    proposal = proposals(findings)[0]
    assert proposal.approval_required is True
    assert proposal.blast_radius == "single-resource"


def test_kpis_do_not_claim_automatic_remediation():
    resource = vm(backupEnabled=False)
    report = kpis([resource], evaluate([resource]))
    assert report["vm_backup_coverage_pct"] == 0
    assert report["automatic_changes_executed"] == 0

