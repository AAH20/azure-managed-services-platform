from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class RecoveryContractError(ValueError):
    """Raised when a recovery contract is unsafe or internally inconsistent."""


@dataclass(frozen=True)
class RecoveryComponent:
    component_id: str
    kind: str
    depends_on: tuple[str, ...]
    validation: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryContract:
    customer_id: str
    workload_id: str
    rto_minutes: int
    rpo_minutes: int
    monthly_revenue_usd: float
    isolated_network_required: bool
    production_failover_allowed: bool
    required_approvers: tuple[str, ...]
    components: tuple[RecoveryComponent, ...]

    @classmethod
    def from_dict(cls, value: dict) -> RecoveryContract:
        objectives = value["objectives"]
        approval = value["approval"]
        return cls(
            customer_id=value["customer_id"],
            workload_id=value["workload_id"],
            rto_minutes=int(objectives["rto_minutes"]),
            rpo_minutes=int(objectives["rpo_minutes"]),
            monthly_revenue_usd=float(value.get("monthly_revenue_usd", 0)),
            isolated_network_required=bool(approval["isolated_network_required"]),
            production_failover_allowed=bool(approval["production_failover_allowed"]),
            required_approvers=tuple(approval["required_approvers"]),
            components=tuple(
                RecoveryComponent(
                    component_id=item["component_id"],
                    kind=item["kind"],
                    depends_on=tuple(item.get("depends_on", [])),
                    validation=tuple(item.get("validation", [])),
                )
                for item in value["components"]
            ),
        )


@dataclass(frozen=True)
class DrillEvent:
    component_id: str
    started_minute: int
    completed_minute: int
    recovery_point_age_minutes: int
    validation_results: dict[str, bool]
    temporary_cost_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> DrillEvent:
        return cls(
            component_id=value["component_id"],
            started_minute=int(value["started_minute"]),
            completed_minute=int(value["completed_minute"]),
            recovery_point_age_minutes=int(value["recovery_point_age_minutes"]),
            validation_results={
                str(key): bool(result) for key, result in value["validation_results"].items()
            },
            temporary_cost_usd=float(value.get("temporary_cost_usd", 0)),
        )


@dataclass(frozen=True)
class RecoveryFinding:
    finding_id: str
    severity: str
    component_id: str
    objective: str
    observed: str
    required: str
    remediation: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def recovery_order(contract: RecoveryContract) -> list[str]:
    components = {item.component_id: item for item in contract.components}
    if len(components) != len(contract.components):
        raise RecoveryContractError("component IDs must be unique")
    for component in contract.components:
        unknown = set(component.depends_on) - components.keys()
        if unknown:
            raise RecoveryContractError(
                f"component {component.component_id!r} has unknown dependencies: {sorted(unknown)}"
            )
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(component_id: str) -> None:
        if component_id in visiting:
            raise RecoveryContractError("recovery dependency graph contains a cycle")
        if component_id in visited:
            return
        visiting.add(component_id)
        for dependency in sorted(components[component_id].depends_on):
            visit(dependency)
        visiting.remove(component_id)
        visited.add(component_id)
        order.append(component_id)

    for component_id in sorted(components):
        visit(component_id)
    return order


def validate_drill_authority(
    contract: RecoveryContract,
    *,
    isolated_network: bool,
    requested_production_failover: bool,
    approvals: set[str],
) -> None:
    if contract.isolated_network_required and not isolated_network:
        raise RecoveryContractError("contract requires an isolated recovery network")
    if requested_production_failover and not contract.production_failover_allowed:
        raise RecoveryContractError("production failover is prohibited by this contract")
    missing = set(contract.required_approvers) - approvals
    if missing:
        raise RecoveryContractError(f"missing required approvals: {sorted(missing)}")


def evaluate_drill(
    contract: RecoveryContract, events: list[DrillEvent]
) -> dict[str, object]:
    order = recovery_order(contract)
    event_by_component = {event.component_id: event for event in events}
    findings: list[RecoveryFinding] = []
    completed = [event.completed_minute for event in events]
    actual_rto = max(completed, default=0)
    actual_rpo = max((event.recovery_point_age_minutes for event in events), default=0)

    for component in contract.components:
        event = event_by_component.get(component.component_id)
        if event is None:
            findings.append(
                _finding(
                    "critical",
                    component.component_id,
                    "component recovery",
                    "missing",
                    "completed drill event",
                    "Add the component to the recovery drill and rerun validation.",
                )
            )
            continue
        for dependency in component.depends_on:
            dependency_event = event_by_component.get(dependency)
            if dependency_event and event.started_minute < dependency_event.completed_minute:
                findings.append(
                    _finding(
                        "high",
                        component.component_id,
                        "dependency order",
                        f"started at {event.started_minute}m",
                        f"after {dependency} completed at {dependency_event.completed_minute}m",
                        "Correct the recovery stage ordering and rerun the drill.",
                    )
                )
        for validation in component.validation:
            if not event.validation_results.get(validation, False):
                findings.append(
                    _finding(
                        "critical" if validation == "synthetic_transaction" else "high",
                        component.component_id,
                        validation,
                        "failed or absent",
                        "passed",
                        "Repair the failed dependency or assertion and repeat the isolated drill.",
                    )
                )

    if actual_rto > contract.rto_minutes:
        findings.append(
            _finding(
                "critical",
                contract.workload_id,
                "RTO",
                f"{actual_rto} minutes",
                f"<= {contract.rto_minutes} minutes",
                "Remove recovery bottlenecks or renegotiate the workload RTO.",
            )
        )
    if actual_rpo > contract.rpo_minutes:
        findings.append(
            _finding(
                "critical",
                contract.workload_id,
                "RPO",
                f"{actual_rpo} minutes",
                f"<= {contract.rpo_minutes} minutes",
                "Increase protection frequency or select a recovery technology matching the RPO.",
            )
        )

    drill_cost = round(sum(event.temporary_cost_usd for event in events), 2)
    hourly_revenue = contract.monthly_revenue_usd / (30 * 24)
    estimated_exposure = round(hourly_revenue * actual_rto / 60, 2)
    validations_required = sum(len(component.validation) for component in contract.components)
    validations_passed = sum(
        event.validation_results.get(validation, False)
        for component in contract.components
        if (event := event_by_component.get(component.component_id))
        for validation in component.validation
    )
    return {
        "schema_version": "1.0",
        "customer_id": contract.customer_id,
        "workload_id": contract.workload_id,
        "recovery_order": order,
        "objectives": {"rto_minutes": contract.rto_minutes, "rpo_minutes": contract.rpo_minutes},
        "observed": {
            "actual_rto_minutes": actual_rto,
            "actual_rpo_minutes": actual_rpo,
            "drill_cost_usd": drill_cost,
            "estimated_revenue_exposure_usd": estimated_exposure,
            "validations_passed": validations_passed,
            "validations_required": validations_required,
        },
        "outcome": "passed" if not findings else "failed",
        "findings": [item.to_dict() for item in findings],
        "cloud_mutations_executed": 0,
        "limitations": [
            "Revenue exposure is a prioritization estimate, not a guaranteed loss.",
            "Synthetic event replay does not prove a live Azure workload is recoverable.",
        ],
    }


def _finding(
    severity: str,
    component_id: str,
    objective: str,
    observed: str,
    required: str,
    remediation: str,
) -> RecoveryFinding:
    seed = f"{component_id}:{objective}:{observed}:{required}"
    return RecoveryFinding(
        finding_id="recovery-" + sha256(seed.encode()).hexdigest()[:12],
        severity=severity,
        component_id=component_id,
        objective=objective,
        observed=observed,
        required=required,
        remediation=remediation,
    )
