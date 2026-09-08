from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class DataReliabilityError(ValueError):
    """Raised when a data contract, observation or lineage graph is invalid."""


@dataclass(frozen=True)
class FieldContract:
    name: str
    data_type: str
    unit: str
    nullable: bool
    minimum: float
    maximum: float

    @classmethod
    def from_dict(cls, value: dict) -> FieldContract:
        return cls(
            name=value["name"], data_type=value["data_type"], unit=value["unit"],
            nullable=bool(value["nullable"]), minimum=float(value["minimum"]),
            maximum=float(value["maximum"]),
        )


@dataclass(frozen=True)
class DataProductContract:
    product_id: str
    owner: str
    maximum_age_minutes: int
    minimum_completeness_pct: float
    maximum_reconciliation_variance_pct: float
    contribution_margin_per_hour_usd: float
    fields: tuple[FieldContract, ...]

    @classmethod
    def from_dict(cls, value: dict) -> DataProductContract:
        return cls(
            product_id=value["product_id"], owner=value["owner"],
            maximum_age_minutes=int(value["maximum_age_minutes"]),
            minimum_completeness_pct=float(value["minimum_completeness_pct"]),
            maximum_reconciliation_variance_pct=float(
                value["maximum_reconciliation_variance_pct"]
            ),
            contribution_margin_per_hour_usd=float(
                value["contribution_margin_per_hour_usd"]
            ),
            fields=tuple(FieldContract.from_dict(item) for item in value["fields"]),
        )


@dataclass(frozen=True)
class FieldObservation:
    name: str
    data_type: str
    unit: str
    null_count: int
    observed_minimum: float
    observed_maximum: float

    @classmethod
    def from_dict(cls, value: dict) -> FieldObservation:
        return cls(
            name=value["name"], data_type=value["data_type"], unit=value["unit"],
            null_count=int(value["null_count"]),
            observed_minimum=float(value["observed_minimum"]),
            observed_maximum=float(value["observed_maximum"]),
        )


@dataclass(frozen=True)
class ProductObservation:
    product_id: str
    pipeline_status: str
    age_minutes: int
    completeness_pct: float
    reconciliation_variance_pct: float
    fields: tuple[FieldObservation, ...]

    @classmethod
    def from_dict(cls, value: dict) -> ProductObservation:
        return cls(
            product_id=value["product_id"], pipeline_status=value["pipeline_status"],
            age_minutes=int(value["age_minutes"]), completeness_pct=float(value["completeness_pct"]),
            reconciliation_variance_pct=float(value["reconciliation_variance_pct"]),
            fields=tuple(FieldObservation.from_dict(item) for item in value["fields"]),
        )


def downstream_nodes(start: str, edges: list[tuple[str, str]]) -> list[str]:
    graph: dict[str, set[str]] = {}
    for source, target in edges:
        graph.setdefault(source, set()).add(target)
    visited, queue = set(), list(graph.get(start, set()))
    while queue:
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        queue.extend(sorted(graph.get(node, set()) - visited))
    visited.discard(start)
    return sorted(visited)


def evaluate_product(
    contract: DataProductContract, observation: ProductObservation
) -> dict[str, object]:
    failures = []
    if observation.pipeline_status != "succeeded":
        failures.append(f"pipeline status is {observation.pipeline_status!r}")
    if observation.age_minutes > contract.maximum_age_minutes:
        failures.append("freshness SLO is breached")
    if observation.completeness_pct < contract.minimum_completeness_pct:
        failures.append("completeness is below the contract minimum")
    if observation.reconciliation_variance_pct > contract.maximum_reconciliation_variance_pct:
        failures.append("reconciliation variance exceeds the contract maximum")

    observed_fields = {item.name: item for item in observation.fields}
    field_results = []
    for field in contract.fields:
        observed = observed_fields.get(field.name)
        field_failures = []
        if observed is None:
            field_failures.append("field is missing")
        else:
            if observed.data_type != field.data_type:
                field_failures.append("data type changed")
            if observed.unit != field.unit:
                field_failures.append(f"unit changed from {field.unit!r} to {observed.unit!r}")
            if not field.nullable and observed.null_count:
                field_failures.append("non-nullable field contains nulls")
            if observed.observed_minimum < field.minimum or observed.observed_maximum > field.maximum:
                field_failures.append("observed values exceed the contracted range")
        failures.extend(f"{field.name}: {item}" for item in field_failures)
        field_results.append({"contract": asdict(field), "failures": field_failures})
    return {
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "field_results": field_results,
        "technical_pipeline_succeeded": observation.pipeline_status == "succeeded",
    }


def evaluate_data_release(value: dict, *, approvals: set[str]) -> dict[str, object]:
    contracts = [DataProductContract.from_dict(item) for item in value["data_contracts"]]
    observations = [ProductObservation.from_dict(item) for item in value["observations"]]
    contract_by_id = {item.product_id: item for item in contracts}
    observation_by_id = {item.product_id: item for item in observations}
    if not contracts:
        raise DataReliabilityError("at least one data contract is required")
    if len(contract_by_id) != len(contracts):
        raise DataReliabilityError("data product ids must be unique")
    missing = set(contract_by_id) - set(observation_by_id)
    if missing:
        raise DataReliabilityError(f"observations are missing for: {sorted(missing)}")
    edges = [(item["source"], item["target"]) for item in value["lineage_edges"]]
    results, failed_products, affected, margin_at_risk = [], [], set(), 0.0
    for contract in contracts:
        result = evaluate_product(contract, observation_by_id[contract.product_id])
        consumers = downstream_nodes(contract.product_id, edges)
        if result["status"] == "fail":
            failed_products.append(contract.product_id)
            affected.update(consumers)
            margin_at_risk += contract.contribution_margin_per_hour_usd
        results.append({"product": asdict(contract), "evaluation": result,
                        "downstream_consumers": consumers})

    required = set(value["release_contract"]["required_approvers"])
    missing_approvals = required - approvals
    blockers = []
    if failed_products:
        blockers.append(f"failed data products: {sorted(failed_products)}")
    if missing_approvals:
        blockers.append(f"missing required approvals: {sorted(missing_approvals)}")
    proposals = []
    if failed_products:
        proposals = [
            {"action": "quarantine-current-version", "authorization": "proposed"},
            {"action": "serve-last-verified-version", "authorization": "proposed"},
            {"action": "backfill-affected-partitions", "authorization": "approval-required"},
        ]
    decision = "blocked" if blockers else "eligible-for-controlled-publication"
    seed = f"{contracts}:{observations}:{edges}:{decision}:{blockers}"
    return {
        "schema_version": "1.0", "release_id": value["release_id"],
        "customer_id": value["customer_id"], "product_results": results,
        "failed_products": sorted(failed_products), "affected_consumers": sorted(affected),
        "decision": decision, "blockers": blockers, "remediation_proposals": proposals,
        "economics": {"contribution_margin_at_risk_per_hour_usd": round(margin_at_risk, 2),
                      "verified_loss_avoided_usd": 0.0},
        "publication_authorized": False, "automated_decisions_authorized": False,
        "evidence_sha256": sha256(seed.encode()).hexdigest(), "data_mutations_executed": 0,
        "limitations": [
            "The included records, lineage and economics are synthetic.",
            "Remediation entries are proposals and do not alter source or analytical data.",
            "Controlled publication requires validation through Change Assurance.",
        ],
    }
