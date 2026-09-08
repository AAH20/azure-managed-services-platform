from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class CapacityContractError(ValueError):
    """Raised when demand or capacity evidence is incomplete."""


@dataclass(frozen=True)
class DemandForecast:
    peak_units_per_second: float
    upper_bound_units_per_second: float
    horizon_minutes: int
    source_age_minutes: int
    maximum_source_age_minutes: int
    confidence_pct: float
    minimum_confidence_pct: float
    contribution_margin_per_unit_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> DemandForecast:
        forecast = cls(**{key: float(value[key]) for key in (
            "peak_units_per_second", "upper_bound_units_per_second", "source_age_minutes",
            "maximum_source_age_minutes", "confidence_pct", "minimum_confidence_pct",
            "contribution_margin_per_unit_usd",
        )}, horizon_minutes=int(value["horizon_minutes"]))
        if forecast.upper_bound_units_per_second < forecast.peak_units_per_second:
            raise CapacityContractError("forecast upper bound cannot be below the peak")
        return forecast


@dataclass(frozen=True)
class DependencyCapacity:
    dependency_id: str
    capacity_units_per_second: float
    ready_in_minutes: int
    quota_available: bool
    load_test_passed: bool

    @classmethod
    def from_dict(cls, value: dict) -> DependencyCapacity:
        return cls(
            dependency_id=value["dependency_id"],
            capacity_units_per_second=float(value["capacity_units_per_second"]),
            ready_in_minutes=int(value["ready_in_minutes"]),
            quota_available=bool(value["quota_available"]),
            load_test_passed=bool(value["load_test_passed"]),
        )


@dataclass(frozen=True)
class CapacityPortfolio:
    portfolio_id: str
    title: str
    event_cost_usd: float
    implementation_cost_usd: float
    expected_abandonment_loss_usd: float
    expected_revenue_protected_usd: float
    reversible: bool
    dependencies: tuple[DependencyCapacity, ...]

    @classmethod
    def from_dict(cls, value: dict) -> CapacityPortfolio:
        return cls(
            portfolio_id=value["portfolio_id"], title=value["title"],
            event_cost_usd=float(value["event_cost_usd"]),
            implementation_cost_usd=float(value["implementation_cost_usd"]),
            expected_abandonment_loss_usd=float(value["expected_abandonment_loss_usd"]),
            expected_revenue_protected_usd=float(value["expected_revenue_protected_usd"]),
            reversible=bool(value["reversible"]),
            dependencies=tuple(DependencyCapacity.from_dict(item) for item in value["dependencies"]),
        )


def evaluate_portfolio(
    portfolio: CapacityPortfolio,
    forecast: DemandForecast,
    required_dependencies: set[str],
) -> dict[str, object]:
    dependency_by_id = {item.dependency_id: item for item in portfolio.dependencies}
    missing = required_dependencies - set(dependency_by_id)
    failures = [f"missing capacity models: {sorted(missing)}"] if missing else []
    dependency_results = []
    for dependency_id in sorted(required_dependencies & set(dependency_by_id)):
        capacity = dependency_by_id[dependency_id]
        reasons = []
        if capacity.capacity_units_per_second < forecast.upper_bound_units_per_second:
            reasons.append("capacity is below forecast upper bound")
        if capacity.ready_in_minutes > forecast.horizon_minutes:
            reasons.append("capacity cannot become ready before demand")
        if not capacity.quota_available:
            reasons.append("required quota or regional capacity is unavailable")
        if not capacity.load_test_passed:
            reasons.append("load test has not passed")
        failures.extend(f"{dependency_id}: {reason}" for reason in reasons)
        dependency_results.append({**asdict(capacity), "status": "pass" if not reasons else "fail",
                                   "reasons": reasons})
    if not portfolio.reversible:
        failures.append("capacity portfolio is not reversible")
    net_value = (
        portfolio.expected_revenue_protected_usd - portfolio.event_cost_usd
        - portfolio.implementation_cost_usd - portfolio.expected_abandonment_loss_usd
    )
    bottleneck = min(
        (item.capacity_units_per_second for item in portfolio.dependencies), default=0.0
    )
    return {
        "portfolio": asdict(portfolio), "dependency_results": dependency_results,
        "bottleneck_capacity_units_per_second": bottleneck,
        "net_event_value_usd": round(net_value, 2),
        "decision": "eligible-for-load-test" if not failures else "rejected",
        "reasons": failures,
    }


def evaluate_capacity_plan(value: dict, *, approvals: set[str]) -> dict[str, object]:
    forecast = DemandForecast.from_dict(value["forecast"])
    required_dependencies = set(value["required_dependencies"])
    if not required_dependencies:
        raise CapacityContractError("at least one dependency is required")
    forecast_failures = []
    if forecast.source_age_minutes > forecast.maximum_source_age_minutes:
        forecast_failures.append("forecast source is stale")
    if forecast.confidence_pct < forecast.minimum_confidence_pct:
        forecast_failures.append("forecast confidence is below the contract minimum")
    required_approvals = set(value["capacity_contract"]["required_approvers"])
    missing_approvals = required_approvals - approvals
    portfolios = [CapacityPortfolio.from_dict(item) for item in value["portfolios"]]
    if not portfolios:
        raise CapacityContractError("at least one capacity portfolio is required")
    results = [evaluate_portfolio(item, forecast, required_dependencies) for item in portfolios]
    if forecast_failures or missing_approvals:
        extra = list(forecast_failures)
        if missing_approvals:
            extra.append(f"missing required approvals: {sorted(missing_approvals)}")
        for result in results:
            result["decision"] = "rejected"
            result["reasons"].extend(extra)
    eligible = [item for item in results if item["decision"] == "eligible-for-load-test"]
    selected = max(eligible, key=lambda item: item["net_event_value_usd"]) if eligible else None
    seed = f"{forecast}:{required_dependencies}:{results}:{selected}"
    return {
        "schema_version": "1.0", "plan_id": value["plan_id"],
        "workload_id": value["workload_id"], "forecast": asdict(forecast),
        "portfolio_results": results,
        "selected_portfolio_id": selected["portfolio"]["portfolio_id"] if selected else None,
        "decision": "eligible-for-load-test" if selected else "blocked",
        "production_scaling_authorized": False, "next_stage": "isolated-load-test",
        "evidence_sha256": sha256(seed.encode()).hexdigest(), "cloud_mutations_executed": 0,
        "limitations": [
            "The included forecast, capacity and economics are synthetic.",
            "Eligibility authorizes an isolated load test only, not production scaling.",
            "Production requires Change Assurance and post-change FinOps verification.",
        ],
    }
