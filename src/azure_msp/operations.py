from __future__ import annotations

from collections import defaultdict
from hashlib import sha256

from .evaluator import SEVERITY_WEIGHT
from .models import AuditEvent, ChangeProposal, Customer, Finding, Resource, WorkItem, Workload

CRITICALITY_WEIGHT = {"mission-critical": 4, "high": 3, "medium": 2, "low": 1}
ALLOWED_TRANSITIONS = {
    "proposed": {"approve": "approved", "reject": "rejected"},
    "approved": {"start": "implementing", "cancel": "cancelled"},
    "implementing": {"validate": "validated", "rollback": "rolled-back"},
    "validated": {"close": "closed", "rollback": "rolled-back"},
    "rejected": {},
    "cancelled": {},
    "rolled-back": {},
    "closed": {},
}


class TenantBoundaryError(PermissionError):
    """Raised when one customer attempts to access another customer's object."""


class InvalidTransitionError(ValueError):
    """Raised when a work item action is not allowed from its current state."""


def build_workloads(
    customer: Customer, resources: list[Resource], definitions: list[dict]
) -> list[Workload]:
    resources_by_workload: dict[str, list[str]] = defaultdict(list)
    for resource in resources:
        resources_by_workload[resource.tags.get("workload", "unassigned")].append(resource.id)

    workloads: list[Workload] = []
    known = set()
    for item in definitions:
        workload_id = item["workload_id"]
        known.add(workload_id)
        workloads.append(
            Workload(
                workload_id=workload_id,
                customer_id=customer.customer_id,
                name=item["name"],
                owner=item["owner"],
                criticality=item["criticality"],
                monthly_revenue_usd=float(item.get("monthly_revenue_usd", 0)),
                slo_pct=float(item.get("slo_pct", 99.9)),
                rto_minutes=int(item.get("rto_minutes", 240)),
                rpo_minutes=int(item.get("rpo_minutes", 1440)),
                resource_ids=tuple(sorted(resources_by_workload.get(workload_id, []))),
            )
        )
    if "unassigned" in resources_by_workload or any(key not in known for key in resources_by_workload):
        unassigned = [
            resource_id
            for key, ids in resources_by_workload.items()
            if key not in known
            for resource_id in ids
        ]
        if unassigned:
            workloads.append(
                Workload(
                    workload_id="unassigned",
                    customer_id=customer.customer_id,
                    name="Unassigned resources",
                    owner="unassigned",
                    criticality="low",
                    monthly_revenue_usd=0,
                    slo_pct=0,
                    rto_minutes=0,
                    rpo_minutes=0,
                    resource_ids=tuple(sorted(unassigned)),
                )
            )
    return sorted(workloads, key=lambda item: item.workload_id)


def build_work_queue(
    customer: Customer,
    workloads: list[Workload],
    findings: list[Finding],
    change_proposals: list[ChangeProposal],
) -> list[WorkItem]:
    workload_by_resource = {
        resource_id: workload
        for workload in workloads
        for resource_id in workload.resource_ids
    }
    proposal_by_key = {
        (proposal.control_id, proposal.resource_id): proposal for proposal in change_proposals
    }
    queue: list[WorkItem] = []
    for finding in findings:
        workload = workload_by_resource.get(finding.resource_id)
        workload_id = workload.workload_id if workload else "unassigned"
        criticality = CRITICALITY_WEIGHT.get(workload.criticality if workload else "low", 1)
        revenue = workload.monthly_revenue_usd if workload else 0
        severity = SEVERITY_WEIGHT[finding.severity]
        exposure = round(revenue * min(0.25, severity * criticality / 100), 2)
        score = round(severity * 10 + criticality * 5 + min(exposure / 1000, 20), 2)
        proposal = proposal_by_key[(finding.control_id, finding.resource_id)]
        queue.append(
            WorkItem(
                work_item_id=f"work-{proposal.proposal_id.removeprefix('change-')}",
                customer_id=customer.customer_id,
                workload_id=workload_id,
                proposal=proposal,
                priority_score=score,
                estimated_monthly_exposure_usd=exposure,
            )
        )
    return sorted(queue, key=lambda item: (-item.priority_score, item.work_item_id))


def transition(
    item: WorkItem,
    *,
    authenticated_customer_id: str,
    action: str,
    actor: str,
    reason: str,
) -> AuditEvent:
    if authenticated_customer_id != item.customer_id:
        raise TenantBoundaryError("work item does not belong to the authenticated customer")
    if not actor.strip() or not reason.strip():
        raise ValueError("actor and reason are required for every state transition")
    previous = item.status
    target = ALLOWED_TRANSITIONS.get(previous, {}).get(action)
    if target is None:
        raise InvalidTransitionError(f"action {action!r} is not allowed from {previous!r}")
    event_seed = f"{item.work_item_id}:{previous}:{target}:{actor}:{reason}"
    event_id = "audit-" + sha256(event_seed.encode("utf-8")).hexdigest()[:16]
    item.status = target
    return AuditEvent.create(
        event_id=event_id,
        customer_id=item.customer_id,
        work_item_id=item.work_item_id,
        actor=actor,
        action=action,
        previous_status=previous,
        new_status=target,
        reason=reason,
    )
