from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    def from_dict(cls, value: dict[str, Any]) -> "Resource":
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

