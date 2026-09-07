from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class FinOpsContractError(ValueError):
    """Raised when financial evidence cannot support a defensible decision."""


@dataclass(frozen=True)
class CostRecord:
    period: str
    resource_id: str
    service: str
    cost_usd: float
    allocation: str
    usage_driver: str | None = None

    @classmethod
    def from_dict(cls, value: dict) -> CostRecord:
        return cls(
            period=value["period"],
            resource_id=value["resource_id"],
            service=value["service"],
            cost_usd=float(value["cost_usd"]),
            allocation=value["allocation"],
            usage_driver=value.get("usage_driver"),
        )


@dataclass(frozen=True)
class WorkloadDemand:
    period: str
    workload_id: str
    successful_transactions: int
    allocation_drivers: dict[str, float]

    @classmethod
    def from_dict(cls, value: dict) -> WorkloadDemand:
        return cls(
            period=value["period"],
            workload_id=value["workload_id"],
            successful_transactions=int(value["successful_transactions"]),
            allocation_drivers={key: float(amount) for key, amount in value["allocation_drivers"].items()},
        )


@dataclass(frozen=True)
class OptimizationOption:
    option_id: str
    title: str
    expected_monthly_saving_usd: float
    implementation_cost_usd: float
    reliability_verified: bool
    recovery_verified: bool
    reversible: bool
    commitment_months: int

    @classmethod
    def from_dict(cls, value: dict) -> OptimizationOption:
        return cls(
            option_id=value["option_id"],
            title=value["title"],
            expected_monthly_saving_usd=float(value["expected_monthly_saving_usd"]),
            implementation_cost_usd=float(value.get("implementation_cost_usd", 0)),
            reliability_verified=bool(value["reliability_verified"]),
            recovery_verified=bool(value["recovery_verified"]),
            reversible=bool(value["reversible"]),
            commitment_months=int(value.get("commitment_months", 0)),
        )


def _allocate_costs(
    records: list[CostRecord], demands: list[WorkloadDemand]
) -> tuple[dict[str, float], float]:
    workloads = {item.workload_id: item for item in demands}
    if not workloads:
        raise FinOpsContractError("at least one workload demand record is required")
    allocated = {workload_id: 0.0 for workload_id in workloads}
    unallocated = 0.0
    for record in records:
        if record.cost_usd < 0:
            raise FinOpsContractError("cost records cannot be negative")
        if record.allocation in workloads:
            allocated[record.allocation] += record.cost_usd
            continue
        if record.allocation != "shared" or not record.usage_driver:
            unallocated += record.cost_usd
            continue
        weights = {
            workload_id: demand.allocation_drivers.get(record.usage_driver, 0.0)
            for workload_id, demand in workloads.items()
        }
        total_weight = sum(weights.values())
        if total_weight <= 0:
            unallocated += record.cost_usd
            continue
        for workload_id, weight in weights.items():
            allocated[workload_id] += record.cost_usd * weight / total_weight
    return ({key: round(value, 2) for key, value in allocated.items()}, round(unallocated, 2))


def _evaluate_option(option: OptimizationOption, required_approvers: set[str], approvals: set[str]):
    reasons = []
    if not option.reliability_verified:
        reasons.append("reliability impact is not verified")
    if not option.recovery_verified:
        reasons.append("recovery impact is not verified")
    if not option.reversible and option.commitment_months == 0:
        reasons.append("irreversible option has no declared commitment term")
    missing = required_approvers - approvals
    if missing:
        reasons.append(f"missing required approvals: {sorted(missing)}")
    status = "eligible-for-change-assurance" if not reasons else "rejected"
    return {
        **asdict(option),
        "decision": status,
        "reasons": reasons,
        "expected_first_year_net_value_usd": round(
            option.expected_monthly_saving_usd * 12 - option.implementation_cost_usd, 2
        ),
        "verified_saving_usd": 0.0,
    }


def evaluate_value_realization(
    value: dict,
    *,
    approvals: set[str],
) -> dict[str, object]:
    records = [CostRecord.from_dict(item) for item in value["cost_records"]]
    demands = [WorkloadDemand.from_dict(item) for item in value["workload_demand"]]
    periods = {item.period for item in records} | {item.period for item in demands}
    if len(periods) != 1:
        raise FinOpsContractError("all cost and demand records must use one evaluation period")
    allocated, unallocated = _allocate_costs(records, demands)
    total_cost = round(sum(item.cost_usd for item in records), 2)
    transaction_metrics = {}
    for demand in demands:
        if demand.successful_transactions <= 0:
            raise FinOpsContractError("successful transactions must be greater than zero")
        workload_cost = allocated[demand.workload_id]
        transaction_metrics[demand.workload_id] = {
            "allocated_cost_usd": workload_cost,
            "successful_transactions": demand.successful_transactions,
            "cost_per_successful_transaction_usd": round(
                workload_cost / demand.successful_transactions, 6
            ),
        }
    required_approvers = set(value["decision_contract"]["required_approvers"])
    options = [
        _evaluate_option(OptimizationOption.from_dict(item), required_approvers, approvals)
        for item in value["optimization_options"]
    ]
    coverage = 100.0 if total_cost == 0 else round((total_cost - unallocated) / total_cost * 100, 2)
    evidence_seed = f"{periods}:{records}:{demands}:{options}"
    return {
        "schema_version": "1.0",
        "evaluation_id": value["evaluation_id"],
        "customer_id": value["customer_id"],
        "period": next(iter(periods)),
        "economics": {
            "total_cost_usd": total_cost,
            "allocated_cost_usd": round(total_cost - unallocated, 2),
            "unallocated_cost_usd": unallocated,
            "allocation_coverage_pct": coverage,
            "workloads": transaction_metrics,
        },
        "optimization_decisions": options,
        "verified_savings_ledger": [],
        "verification_status": "pending-post-change-billing-evidence",
        "evidence_sha256": sha256(evidence_seed.encode()).hexdigest(),
        "cloud_mutations_executed": 0,
        "limitations": [
            "The fixture is synthetic and is not evidence of a live Azure bill or customer saving.",
            "Eligible options still require the separate Change Assurance workflow.",
            "Savings enter the ledger only after normalized post-change billing verification.",
        ],
    }
