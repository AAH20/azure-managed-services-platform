from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class ChangeContractError(ValueError):
    """Raised when a change contract is unsafe or incomplete."""


@dataclass(frozen=True)
class PlannedOperation:
    resource_id: str
    action: str
    before: dict[str, object]
    after: dict[str, object]


@dataclass(frozen=True)
class ChangeContract:
    change_id: str
    customer_id: str
    workload_id: str
    production: bool
    expected_monthly_saving_usd: float
    maximum_p95_latency_ms: float
    maximum_error_rate_pct: float
    minimum_transaction_success_pct: float
    required_approvers: tuple[str, ...]
    exposure_rings: tuple[str, ...]
    monitoring_verified: bool
    recovery_verified: bool
    rollback_steps: tuple[str, ...]
    operations: tuple[PlannedOperation, ...]

    @classmethod
    def from_dict(cls, value: dict) -> ChangeContract:
        intent = value["intent"]
        risk = value["risk"]
        deployment = value["deployment"]
        approval = value["approval"]
        validation = value["validation"]
        return cls(
            change_id=value["change_id"],
            customer_id=value["customer_id"],
            workload_id=value["workload_id"],
            production=bool(risk["production"]),
            expected_monthly_saving_usd=float(intent["expected_monthly_saving_usd"]),
            maximum_p95_latency_ms=float(validation["maximum_p95_latency_ms"]),
            maximum_error_rate_pct=float(validation["maximum_error_rate_pct"]),
            minimum_transaction_success_pct=float(validation["minimum_transaction_success_pct"]),
            required_approvers=tuple(approval["required"]),
            exposure_rings=tuple(deployment["rings"]),
            monitoring_verified=bool(risk["monitoring_verified"]),
            recovery_verified=bool(risk["recovery_verified"]),
            rollback_steps=tuple(risk["rollback_steps"]),
            operations=tuple(
                PlannedOperation(
                    resource_id=item["resource_id"],
                    action=item["action"],
                    before=item.get("before", {}),
                    after=item.get("after", {}),
                )
                for item in value["operations"]
            ),
        )


@dataclass(frozen=True)
class RingObservation:
    ring: str
    p95_latency_ms: float
    error_rate_pct: float
    transaction_success_pct: float
    observed_monthly_cost_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> RingObservation:
        return cls(
            ring=value["ring"],
            p95_latency_ms=float(value["p95_latency_ms"]),
            error_rate_pct=float(value["error_rate_pct"]),
            transaction_success_pct=float(value["transaction_success_pct"]),
            observed_monthly_cost_usd=float(value["observed_monthly_cost_usd"]),
        )


def validate_contract(contract: ChangeContract, approvals: set[str]) -> None:
    if not contract.operations:
        raise ChangeContractError("at least one planned operation is required")
    if contract.production and contract.exposure_rings[0] == "production":
        raise ChangeContractError("production cannot be the first exposure ring")
    if len(set(contract.exposure_rings)) != len(contract.exposure_rings):
        raise ChangeContractError("exposure rings must be unique")
    if contract.production and "production" not in contract.exposure_rings:
        raise ChangeContractError("production change requires a production exposure ring")
    if not contract.monitoring_verified:
        raise ChangeContractError("monitoring coverage must be verified")
    if not contract.recovery_verified:
        raise ChangeContractError("recovery coverage must be verified")
    if not contract.rollback_steps:
        raise ChangeContractError("rollback steps are required")
    missing = set(contract.required_approvers) - approvals
    if missing:
        raise ChangeContractError(f"missing required approvals: {sorted(missing)}")
    destructive = [item.resource_id for item in contract.operations if item.action == "delete"]
    if destructive:
        raise ChangeContractError(f"destructive operations require a separate contract: {destructive}")


def evaluate_rollout(
    contract: ChangeContract,
    observations: list[RingObservation],
    *,
    approvals: set[str],
) -> dict[str, object]:
    validate_contract(contract, approvals)
    observation_by_ring = {item.ring: item for item in observations}
    decisions: list[dict[str, object]] = []
    promoted: list[str] = []
    halted_at: str | None = None
    reasons: list[str] = []

    for ring in contract.exposure_rings:
        observation = observation_by_ring.get(ring)
        if observation is None:
            halted_at = ring
            reasons = ["mandatory observation is missing"]
        else:
            reasons = []
            if observation.p95_latency_ms > contract.maximum_p95_latency_ms:
                reasons.append(
                    f"p95 latency {observation.p95_latency_ms:g}ms exceeds "
                    f"{contract.maximum_p95_latency_ms:g}ms"
                )
            if observation.error_rate_pct > contract.maximum_error_rate_pct:
                reasons.append(
                    f"error rate {observation.error_rate_pct:g}% exceeds "
                    f"{contract.maximum_error_rate_pct:g}%"
                )
            if observation.transaction_success_pct < contract.minimum_transaction_success_pct:
                reasons.append(
                    f"transaction success {observation.transaction_success_pct:g}% is below "
                    f"{contract.minimum_transaction_success_pct:g}%"
                )
            if reasons:
                halted_at = ring
            else:
                promoted.append(ring)
        decisions.append(
            {
                "ring": ring,
                "decision": "halt-and-rollback" if reasons else "promote",
                "reasons": reasons,
            }
        )
        if reasons:
            break

    last_observed = observation_by_ring.get(halted_at) if halted_at else None
    realized_saving = 0.0
    if last_observed and halted_at == "production" and not reasons:
        realized_saving = contract.expected_monthly_saving_usd
    outcome = "halted" if halted_at else "completed"
    evidence_seed = f"{contract.change_id}:{outcome}:{halted_at}:{decisions}"
    return {
        "schema_version": "1.0",
        "change_id": contract.change_id,
        "customer_id": contract.customer_id,
        "workload_id": contract.workload_id,
        "outcome": outcome,
        "promoted_rings": promoted,
        "halted_at": halted_at,
        "decisions": decisions,
        "economics": {
            "expected_monthly_saving_usd": contract.expected_monthly_saving_usd,
            "realized_monthly_saving_usd": realized_saving,
            "rollout_accepted": outcome == "completed",
            "saving_verified": False,
        },
        "rollback": {
            "required": outcome == "halted",
            "steps": list(contract.rollback_steps) if outcome == "halted" else [],
        },
        "evidence_sha256": sha256(evidence_seed.encode()).hexdigest(),
        "cloud_mutations_executed": 0,
        "limitations": [
            "This report evaluates supplied observations and does not execute a deployment.",
            "Expected savings require post-billing verification before being reported as realized.",
        ],
    }


def operation_dict(operation: PlannedOperation) -> dict[str, object]:
    return asdict(operation)
