from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class AIValueContractError(ValueError):
    """Raised when AI value evidence cannot support an economic decision."""


@dataclass(frozen=True)
class RateCard:
    provider: str
    model: str
    input_per_million_usd: float
    output_per_million_usd: float
    gpu_hour_usd: float
    tool_call_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> RateCard:
        rates = cls(
            provider=value["provider"],
            model=value["model"],
            input_per_million_usd=float(value.get("input_per_million_usd", 0)),
            output_per_million_usd=float(value.get("output_per_million_usd", 0)),
            gpu_hour_usd=float(value.get("gpu_hour_usd", 0)),
            tool_call_usd=float(value.get("tool_call_usd", 0)),
        )
        if min(
            rates.input_per_million_usd,
            rates.output_per_million_usd,
            rates.gpu_hour_usd,
            rates.tool_call_usd,
        ) < 0:
            raise AIValueContractError("rate-card values cannot be negative")
        return rates


@dataclass(frozen=True)
class OutcomeEvent:
    event_id: str
    trace_id: str
    tenant_id: str
    workflow_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    gpu_seconds: float
    tool_calls: int
    attempt: int
    accepted: bool
    evaluation_passed: bool
    latency_ms: float
    business_value_usd: float
    revenue_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> OutcomeEvent:
        event = cls(
            event_id=value["event_id"],
            trace_id=value["trace_id"],
            tenant_id=value["tenant_id"],
            workflow_id=value["workflow_id"],
            provider=value["provider"],
            model=value["model"],
            input_tokens=int(value["input_tokens"]),
            output_tokens=int(value["output_tokens"]),
            gpu_seconds=float(value.get("gpu_seconds", 0)),
            tool_calls=int(value.get("tool_calls", 0)),
            attempt=int(value.get("attempt", 1)),
            accepted=bool(value["accepted"]),
            evaluation_passed=bool(value["evaluation_passed"]),
            latency_ms=float(value["latency_ms"]),
            business_value_usd=float(value.get("business_value_usd", 0)),
            revenue_usd=float(value.get("revenue_usd", 0)),
        )
        numeric = (
            event.input_tokens,
            event.output_tokens,
            event.gpu_seconds,
            event.tool_calls,
            event.latency_ms,
            event.business_value_usd,
            event.revenue_usd,
        )
        if min(numeric) < 0 or event.attempt < 1:
            raise AIValueContractError("event quantities and values cannot be negative")
        return event


@dataclass(frozen=True)
class RoutingCandidate:
    candidate_id: str
    provider: str
    model: str
    region: str
    projected_cost_per_accepted_outcome_usd: float
    projected_evaluation_pass_rate_pct: float
    projected_p95_latency_ms: float
    reversible: bool

    @classmethod
    def from_dict(cls, value: dict) -> RoutingCandidate:
        return cls(
            candidate_id=value["candidate_id"],
            provider=value["provider"],
            model=value["model"],
            region=value["region"],
            projected_cost_per_accepted_outcome_usd=float(
                value["projected_cost_per_accepted_outcome_usd"]
            ),
            projected_evaluation_pass_rate_pct=float(
                value["projected_evaluation_pass_rate_pct"]
            ),
            projected_p95_latency_ms=float(value["projected_p95_latency_ms"]),
            reversible=bool(value["reversible"]),
        )


def _event_cost(event: OutcomeEvent, rate: RateCard) -> float:
    return (
        event.input_tokens / 1_000_000 * rate.input_per_million_usd
        + event.output_tokens / 1_000_000 * rate.output_per_million_usd
        + event.gpu_seconds / 3600 * rate.gpu_hour_usd
        + event.tool_calls * rate.tool_call_usd
    )


def _percentile_95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]


def _evaluate_candidate(candidate: RoutingCandidate, contract: dict, approvals: set[str]) -> dict:
    reasons = []
    if candidate.region not in set(contract["allowed_regions"]):
        reasons.append("candidate violates the allowed-region contract")
    if candidate.projected_evaluation_pass_rate_pct < float(contract["minimum_pass_rate_pct"]):
        reasons.append("candidate evaluation pass rate is below the quality floor")
    if candidate.projected_p95_latency_ms > float(contract["maximum_p95_latency_ms"]):
        reasons.append("candidate latency exceeds the SLO")
    if not candidate.reversible:
        reasons.append("candidate rollout is not reversible")
    missing = set(contract["required_approvers"]) - approvals
    if missing:
        reasons.append(f"missing required approvals: {sorted(missing)}")
    return {
        **asdict(candidate),
        "decision": "eligible-for-shadow-test" if not reasons else "rejected",
        "reasons": reasons,
        "routing_change_executed": False,
    }


def evaluate_ai_value(value: dict, *, approvals: set[str]) -> dict[str, object]:
    rates = [RateCard.from_dict(item) for item in value["rate_cards"]]
    rate_index = {(item.provider, item.model): item for item in rates}
    events = [OutcomeEvent.from_dict(item) for item in value["outcome_events"]]
    if not events:
        raise AIValueContractError("at least one outcome event is required")
    if len({item.event_id for item in events}) != len(events):
        raise AIValueContractError("event IDs must be unique")

    shared_cost = float(value["shared_platform_cost_usd"])
    if shared_cost < 0:
        raise AIValueContractError("shared platform cost cannot be negative")
    direct_costs = []
    for event in events:
        rate = rate_index.get((event.provider, event.model))
        if rate is None:
            raise AIValueContractError(f"missing rate card for {event.provider}/{event.model}")
        direct_costs.append(_event_cost(event, rate))

    allocation_weight = sum(item.input_tokens + item.output_tokens for item in events)
    workflow_rows: dict[tuple[str, str], dict] = {}
    retry_waste = 0.0
    for event, direct_cost in zip(events, direct_costs, strict=True):
        weight = event.input_tokens + event.output_tokens
        allocated_shared = 0 if allocation_weight == 0 else shared_cost * weight / allocation_weight
        total_event_cost = direct_cost + allocated_shared
        key = (event.tenant_id, event.workflow_id)
        row = workflow_rows.setdefault(
            key,
            {
                "tenant_id": event.tenant_id,
                "workflow_id": event.workflow_id,
                "events": 0,
                "accepted_outcomes": 0,
                "evaluation_passes": 0,
                "cost_usd": 0.0,
                "business_value_usd": 0.0,
                "revenue_usd": 0.0,
                "latencies": [],
            },
        )
        row["events"] += 1
        row["accepted_outcomes"] += int(event.accepted)
        row["evaluation_passes"] += int(event.evaluation_passed)
        row["cost_usd"] += total_event_cost
        row["business_value_usd"] += event.business_value_usd
        row["revenue_usd"] += event.revenue_usd
        row["latencies"].append(event.latency_ms)
        if event.attempt > 1 or not event.accepted:
            retry_waste += total_event_cost

    workflows = []
    for row in workflow_rows.values():
        accepted = row["accepted_outcomes"]
        cost = row["cost_usd"]
        revenue = row["revenue_usd"]
        workflows.append(
            {
                "tenant_id": row["tenant_id"],
                "workflow_id": row["workflow_id"],
                "events": row["events"],
                "accepted_outcomes": accepted,
                "evaluation_pass_rate_pct": round(
                    row["evaluation_passes"] / row["events"] * 100, 2
                ),
                "p95_latency_ms": round(_percentile_95(row["latencies"]), 2),
                "allocated_cost_usd": round(cost, 4),
                "cost_per_accepted_outcome_usd": (
                    None if accepted == 0 else round(cost / accepted, 4)
                ),
                "business_value_usd": round(row["business_value_usd"], 2),
                "revenue_usd": round(revenue, 2),
                "gross_margin_pct": 0 if revenue == 0 else round((revenue - cost) / revenue * 100, 2),
            }
        )

    total_cost = sum(direct_costs) + shared_cost
    candidates = [
        _evaluate_candidate(RoutingCandidate.from_dict(item), value["routing_contract"], approvals)
        for item in value["routing_candidates"]
    ]
    eligible = [item for item in candidates if item["decision"] == "eligible-for-shadow-test"]
    selected = min(eligible, key=lambda item: item["projected_cost_per_accepted_outcome_usd"]) if eligible else None
    seed = f"{events}:{rates}:{workflows}:{candidates}"
    return {
        "schema_version": "1.0",
        "evaluation_id": value["evaluation_id"],
        "period": value["period"],
        "economics": {
            "total_cost_usd": round(total_cost, 4),
            "direct_cost_usd": round(sum(direct_costs), 4),
            "shared_platform_cost_usd": round(shared_cost, 4),
            "retry_and_rejected_waste_usd": round(retry_waste, 4),
            "retry_and_rejected_waste_pct": (
                0 if total_cost == 0 else round(retry_waste / total_cost * 100, 2)
            ),
            "allocation_coverage_pct": 100.0,
            "workflows": sorted(workflows, key=lambda item: (item["tenant_id"], item["workflow_id"])),
        },
        "routing_candidates": candidates,
        "selected_shadow_candidate_id": selected["candidate_id"] if selected else None,
        "routing_change_executed": False,
        "verified_savings_ledger": [],
        "evidence_sha256": sha256(seed.encode()).hexdigest(),
        "cloud_mutations_executed": 0,
        "limitations": [
            "The fixture uses synthetic traces, prices, revenue and business-value evidence.",
            "A recommendation authorizes only a shadow test, not a production routing change.",
            "Savings remain empty until post-change billing and outcome evidence are reconciled.",
        ],
    }
