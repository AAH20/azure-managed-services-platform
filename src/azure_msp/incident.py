from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class IncidentContractError(ValueError):
    """Raised when incident evidence or authority is insufficient."""


@dataclass(frozen=True)
class IncidentSignal:
    signal_id: str
    workload_id: str
    source: str
    symptom: str
    observed_value: float
    threshold: float
    minute: int

    @classmethod
    def from_dict(cls, value: dict) -> IncidentSignal:
        return cls(
            signal_id=value["signal_id"],
            workload_id=value["workload_id"],
            source=value["source"],
            symptom=value["symptom"],
            observed_value=float(value["observed_value"]),
            threshold=float(value["threshold"]),
            minute=int(value["minute"]),
        )


@dataclass(frozen=True)
class DiagnosticEvidence:
    diagnostic_id: str
    effect: str
    observation: str
    supports_hypothesis: str | None
    verified_cause: bool
    verification_method: str | None

    @classmethod
    def from_dict(cls, value: dict) -> DiagnosticEvidence:
        return cls(
            diagnostic_id=value["diagnostic_id"],
            effect=value["effect"],
            observation=value["observation"],
            supports_hypothesis=value.get("supports_hypothesis"),
            verified_cause=bool(value.get("verified_cause", False)),
            verification_method=value.get("verification_method"),
        )


@dataclass(frozen=True)
class RestorationObservation:
    minute: int
    p95_latency_ms: float
    error_rate_pct: float
    transaction_success_pct: float

    @classmethod
    def from_dict(cls, value: dict) -> RestorationObservation:
        return cls(
            minute=int(value["minute"]),
            p95_latency_ms=float(value["p95_latency_ms"]),
            error_rate_pct=float(value["error_rate_pct"]),
            transaction_success_pct=float(value["transaction_success_pct"]),
        )


@dataclass(frozen=True)
class IncidentContract:
    incident_id: str
    customer_id: str
    workload_id: str
    linked_change_id: str
    normal_transactions_per_minute: float
    contribution_margin_per_transaction_usd: float
    restore_objective_minutes: int
    maximum_p95_latency_ms: float
    maximum_error_rate_pct: float
    minimum_transaction_success_pct: float
    automatic_actions: tuple[str, ...]
    approval_actions: tuple[str, ...]
    prohibited_actions: tuple[str, ...]
    required_approvers: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict) -> IncidentContract:
        business = value["business_context"]
        objectives = value["objectives"]
        authority = value["authority"]
        health = value["restoration_health"]
        return cls(
            incident_id=value["incident_id"],
            customer_id=value["customer_id"],
            workload_id=value["workload_id"],
            linked_change_id=value["linked_change_id"],
            normal_transactions_per_minute=float(business["normal_transactions_per_minute"]),
            contribution_margin_per_transaction_usd=float(
                business["contribution_margin_per_transaction_usd"]
            ),
            restore_objective_minutes=int(objectives["restore_minutes"]),
            maximum_p95_latency_ms=float(health["maximum_p95_latency_ms"]),
            maximum_error_rate_pct=float(health["maximum_error_rate_pct"]),
            minimum_transaction_success_pct=float(health["minimum_transaction_success_pct"]),
            automatic_actions=tuple(authority["automatic"]),
            approval_actions=tuple(authority["approval_required"]),
            prohibited_actions=tuple(authority["prohibited"]),
            required_approvers=tuple(authority["required_approvers"]),
        )


def correlate_signals(contract: IncidentContract, signals: list[IncidentSignal]) -> dict[str, object]:
    relevant = [item for item in signals if item.workload_id == contract.workload_id]
    groups: dict[tuple[str, str], list[IncidentSignal]] = {}
    for signal in relevant:
        groups.setdefault((signal.workload_id, signal.symptom), []).append(signal)
    normalized = []
    for (_, symptom), members in sorted(groups.items()):
        normalized.append(
            {
                "symptom": symptom,
                "sources": sorted({item.source for item in members}),
                "signal_ids": sorted(item.signal_id for item in members),
                "first_observed_minute": min(item.minute for item in members),
                "peak_observed_value": max(item.observed_value for item in members),
                "threshold": members[0].threshold,
            }
        )
    return {
        "raw_signals": len(relevant),
        "normalized_symptoms": normalized,
        "deduplicated_signals": len(relevant) - len(normalized),
    }


def authorize_action(
    contract: IncidentContract, action: str, approvals: set[str]
) -> dict[str, object]:
    if action in contract.prohibited_actions:
        raise IncidentContractError(f"action {action!r} is prohibited")
    if action in contract.automatic_actions:
        return {"action": action, "authorization": "automatic-read-only"}
    if action in contract.approval_actions:
        missing = set(contract.required_approvers) - approvals
        if missing:
            raise IncidentContractError(f"missing required approvals: {sorted(missing)}")
        return {"action": action, "authorization": "human-approved"}
    raise IncidentContractError(f"action {action!r} is not declared in the authority contract")


def evaluate_incident(
    contract: IncidentContract,
    signals: list[IncidentSignal],
    diagnostics: list[DiagnosticEvidence],
    restoration: RestorationObservation,
    *,
    proposed_action: str,
    approvals: set[str],
) -> dict[str, object]:
    correlation = correlate_signals(contract, signals)
    if not correlation["normalized_symptoms"]:
        raise IncidentContractError("no signals map to the contracted workload")
    authority = authorize_action(contract, proposed_action, approvals)
    if any(item.effect != "read" for item in diagnostics):
        raise IncidentContractError("synthetic diagnostic evidence must be read-only")

    hypotheses: dict[str, list[str]] = {}
    verified_causes: list[dict[str, str]] = []
    for diagnostic in diagnostics:
        if diagnostic.supports_hypothesis:
            hypotheses.setdefault(diagnostic.supports_hypothesis, []).append(diagnostic.diagnostic_id)
        if diagnostic.verified_cause:
            if diagnostic.verification_method != "controlled_intervention":
                raise IncidentContractError(
                    "verified cause requires controlled_intervention evidence"
                )
            verified_causes.append(
                {
                    "cause": diagnostic.supports_hypothesis or "unspecified",
                    "evidence": diagnostic.diagnostic_id,
                    "observation": diagnostic.observation,
                    "verification_method": diagnostic.verification_method,
                }
            )

    health_failures = []
    if restoration.p95_latency_ms > contract.maximum_p95_latency_ms:
        health_failures.append("p95_latency")
    if restoration.error_rate_pct > contract.maximum_error_rate_pct:
        health_failures.append("error_rate")
    if restoration.transaction_success_pct < contract.minimum_transaction_success_pct:
        health_failures.append("synthetic_transaction")
    restored = not health_failures
    objective_met = restored and restoration.minute <= contract.restore_objective_minutes
    exposure_minutes = restoration.minute
    estimated_affected_transactions = round(
        contract.normal_transactions_per_minute * exposure_minutes, 2
    )
    estimated_margin_exposure = round(
        estimated_affected_transactions * contract.contribution_margin_per_transaction_usd, 2
    )
    prevention = [
        {
            "title": "Bind database resizing to checkout health gates",
            "type": "change-assurance-contract",
            "linked_change_id": contract.linked_change_id,
        },
        {
            "title": "Require canary transaction evidence before production promotion",
            "type": "pipeline-policy",
            "linked_change_id": contract.linked_change_id,
        },
    ]
    evidence_seed = (
        f"{contract.incident_id}:{correlation}:{hypotheses}:{verified_causes}:"
        f"{asdict(restoration)}:{authority}"
    )
    return {
        "schema_version": "1.0",
        "incident_id": contract.incident_id,
        "customer_id": contract.customer_id,
        "workload_id": contract.workload_id,
        "linked_change_id": contract.linked_change_id,
        "correlation": correlation,
        "facts": [
            {"diagnostic_id": item.diagnostic_id, "observation": item.observation}
            for item in diagnostics
        ],
        "hypotheses": [
            {"hypothesis": hypothesis, "supporting_evidence": sorted(evidence)}
            for hypothesis, evidence in sorted(hypotheses.items())
        ],
        "verified_causes": verified_causes,
        "action": authority,
        "restoration": {
            **asdict(restoration),
            "health_failures": health_failures,
            "service_restored": restored,
            "restore_objective_met": objective_met,
        },
        "economics": {
            "exposure_minutes": exposure_minutes,
            "estimated_affected_transactions": estimated_affected_transactions,
            "estimated_contribution_margin_exposure_usd": estimated_margin_exposure,
            "verified_financial_loss_usd": None,
        },
        "prevention_backlog": prevention,
        "evidence_sha256": sha256(evidence_seed.encode()).hexdigest(),
        "cloud_mutations_executed": 0,
        "limitations": [
            "This is deterministic replay of supplied synthetic evidence.",
            "Estimated exposure is not verified financial loss.",
            "An approved action in this report was not executed against Azure.",
        ],
    }
