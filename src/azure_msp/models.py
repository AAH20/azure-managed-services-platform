from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class Resource:
    id: str
    name: str
    type: str
    location: str
    tags: dict[str, str] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Resource:
        return cls(
            id=value["id"],
            name=value["name"],
            type=value["type"].lower(),
            location=value.get("location", "global"),
            tags=value.get("tags") or {},
            properties=value.get("properties") or {},
        )


@dataclass(frozen=True)
class Finding:
    control_id: str
    resource_id: str
    severity: str
    title: str
    evidence: dict[str, Any]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChangeProposal:
    proposal_id: str
    control_id: str
    resource_id: str
    expected_benefit: str
    blast_radius: str
    approval_required: bool
    validation: list[str]
    rollback: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Customer:
    customer_id: str
    name: str
    tenant_id: str


@dataclass(frozen=True)
class Workload:
    workload_id: str
    customer_id: str
    name: str
    owner: str
    criticality: str
    monthly_revenue_usd: float
    slo_pct: float
    rto_minutes: int
    rpo_minutes: int
    resource_ids: tuple[str, ...]


@dataclass
class WorkItem:
    work_item_id: str
    customer_id: str
    workload_id: str
    proposal: ChangeProposal
    priority_score: float
    estimated_monthly_exposure_usd: float
    status: str = "proposed"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["proposal"] = self.proposal.to_dict()
        return value


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    customer_id: str
    work_item_id: str
    actor: str
    action: str
    previous_status: str
    new_status: str
    reason: str
    recorded_at: str

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        customer_id: str,
        work_item_id: str,
        actor: str,
        action: str,
        previous_status: str,
        new_status: str,
        reason: str,
    ) -> AuditEvent:
        return cls(
            event_id=event_id,
            customer_id=customer_id,
            work_item_id=work_item_id,
            actor=actor,
            action=action,
            previous_status=previous_status,
            new_status=new_status,
            reason=reason,
            recorded_at=datetime.now(UTC).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
