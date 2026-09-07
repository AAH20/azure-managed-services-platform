from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from hashlib import sha256

from .models import ChangeProposal, Finding, Resource

SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
REQUIRED_TAGS = ("owner", "environment", "costCenter")


def evaluate(resources: Iterable[Resource]) -> list[Finding]:
    findings: list[Finding] = []
    for resource in resources:
        missing = [tag for tag in REQUIRED_TAGS if not resource.tags.get(tag)]
        if missing:
            findings.append(
                Finding(
                    control_id="AZ-MSP-001",
                    resource_id=resource.id,
                    severity="medium",
                    title="Resource ownership metadata is incomplete",
                    evidence={"missing_tags": missing, "observed_tags": resource.tags},
                    recommendation="Assign owner, environment and costCenter tags through IaC.",
                )
            )

        if resource.type == "microsoft.compute/virtualmachines":
            if not resource.properties.get("monitoringEnabled", False):
                findings.append(
                    Finding(
                        control_id="AZ-MSP-002",
                        resource_id=resource.id,
                        severity="high",
                        title="Virtual machine has no verified monitoring coverage",
                        evidence={"monitoringEnabled": False},
                        recommendation="Onboard the VM to the approved monitoring baseline.",
                    )
                )
            if not resource.properties.get("backupEnabled", False):
                findings.append(
                    Finding(
                        control_id="AZ-MSP-003",
                        resource_id=resource.id,
                        severity="high",
                        title="Virtual machine has no verified backup coverage",
                        evidence={"backupEnabled": False},
                        recommendation="Select a workload RPO and onboard the VM to Azure Backup.",
                    )
                )
            if int(resource.properties.get("daysSincePatchAssessment", 10_000)) > 7:
                findings.append(
                    Finding(
                        control_id="AZ-MSP-004",
                        resource_id=resource.id,
                        severity="high",
                        title="Patch assessment is stale",
                        evidence={
                            "daysSincePatchAssessment": resource.properties.get(
                                "daysSincePatchAssessment"
                            )
                        },
                        recommendation="Run assessment, review impact and schedule an update window.",
                    )
                )

        if resource.type == "microsoft.storage/storageaccounts" and resource.properties.get(
            "publicNetworkAccess", "Enabled"
        ) != "Disabled":
            findings.append(
                Finding(
                    control_id="AZ-MSP-005",
                    resource_id=resource.id,
                    severity="high",
                    title="Storage account permits public network access",
                    evidence={
                        "publicNetworkAccess": resource.properties.get(
                            "publicNetworkAccess", "Enabled"
                        )
                    },
                    recommendation=(
                        "Validate application dependencies, then use Private Endpoint or scoped rules."
                    ),
                )
            )
    return sorted(
        findings,
        key=lambda item: (-SEVERITY_WEIGHT[item.severity], item.control_id, item.resource_id),
    )


def proposals(findings: Iterable[Finding]) -> list[ChangeProposal]:
    result: list[ChangeProposal] = []
    for finding in findings:
        digest = sha256(
            f"{finding.control_id}:{finding.resource_id}".encode()
        ).hexdigest()[:12]
        result.append(
            ChangeProposal(
                proposal_id=f"change-{digest}",
                control_id=finding.control_id,
                resource_id=finding.resource_id,
                expected_benefit=finding.recommendation,
                blast_radius="single-resource",
                approval_required=True,
                validation=[
                    "Re-query the resource after deployment.",
                    f"Confirm control {finding.control_id} returns no finding.",
                    "Confirm workload health and cost telemetry remain within agreed bounds.",
                ],
                rollback=[
                    "Revert the reviewed IaC change.",
                    "Redeploy the previous known-good version.",
                    "Record rollback evidence and unresolved risk.",
                ],
            )
        )
    return result


def kpis(resources: list[Resource], findings: list[Finding]) -> dict[str, object]:
    severity = Counter(finding.severity for finding in findings)
    denominator = max(len(resources), 1)
    owned = sum(1 for resource in resources if resource.tags.get("owner"))
    vms = [r for r in resources if r.type == "microsoft.compute/virtualmachines"]
    monitored = sum(1 for r in vms if r.properties.get("monitoringEnabled", False))
    protected = sum(1 for r in vms if r.properties.get("backupEnabled", False))
    return {
        "resources_evaluated": len(resources),
        "findings": len(findings),
        "findings_by_severity": dict(sorted(severity.items())),
        "ownership_coverage_pct": round(owned / denominator * 100, 2),
        "vm_monitoring_coverage_pct": round(monitored / max(len(vms), 1) * 100, 2),
        "vm_backup_coverage_pct": round(protected / max(len(vms), 1) * 100, 2),
        "open_change_proposals": len(findings),
        "automatic_changes_executed": 0,
    }
