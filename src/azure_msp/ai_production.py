from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class AIProductionContractError(ValueError):
    """Raised when AI release evidence is incomplete or inconsistent."""


@dataclass(frozen=True)
class AgentCandidate:
    candidate_id: str
    provider: str
    model: str
    requests: int
    completed_workflows: int
    quality_score: float
    groundedness_score: float
    correct_tool_rate_pct: float
    p95_latency_ms: float
    inference_cost_usd: float
    retrieval_cost_usd: float
    revenue_generated_usd: float
    labor_avoided_usd: float
    human_escalation_cost_usd: float
    expected_error_cost_usd: float
    provider_failures: int
    tool_sequences: tuple[tuple[str, ...], ...]

    @classmethod
    def from_dict(cls, value: dict) -> AgentCandidate:
        return cls(
            candidate_id=value["candidate_id"],
            provider=value["provider"],
            model=value["model"],
            requests=int(value["requests"]),
            completed_workflows=int(value["completed_workflows"]),
            quality_score=float(value["quality_score"]),
            groundedness_score=float(value["groundedness_score"]),
            correct_tool_rate_pct=float(value["correct_tool_rate_pct"]),
            p95_latency_ms=float(value["p95_latency_ms"]),
            inference_cost_usd=float(value["inference_cost_usd"]),
            retrieval_cost_usd=float(value["retrieval_cost_usd"]),
            revenue_generated_usd=float(value["revenue_generated_usd"]),
            labor_avoided_usd=float(value["labor_avoided_usd"]),
            human_escalation_cost_usd=float(value["human_escalation_cost_usd"]),
            expected_error_cost_usd=float(value["expected_error_cost_usd"]),
            provider_failures=int(value["provider_failures"]),
            tool_sequences=tuple(tuple(item) for item in value.get("tool_sequences", [])),
        )


@dataclass(frozen=True)
class ReleaseContract:
    baseline_candidate_id: str
    minimum_completion_rate_pct: float
    minimum_quality_score: float
    minimum_groundedness_score: float
    minimum_correct_tool_rate_pct: float
    maximum_p95_latency_ms: float
    maximum_loop_rate_pct: float
    maximum_provider_failure_rate_pct: float
    maximum_steps: int
    maximum_tool_repetitions: int
    required_approvers: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict) -> ReleaseContract:
        return cls(
            baseline_candidate_id=value["baseline_candidate_id"],
            minimum_completion_rate_pct=float(value["minimum_completion_rate_pct"]),
            minimum_quality_score=float(value["minimum_quality_score"]),
            minimum_groundedness_score=float(value["minimum_groundedness_score"]),
            minimum_correct_tool_rate_pct=float(value["minimum_correct_tool_rate_pct"]),
            maximum_p95_latency_ms=float(value["maximum_p95_latency_ms"]),
            maximum_loop_rate_pct=float(value["maximum_loop_rate_pct"]),
            maximum_provider_failure_rate_pct=float(value["maximum_provider_failure_rate_pct"]),
            maximum_steps=int(value["maximum_steps"]),
            maximum_tool_repetitions=int(value["maximum_tool_repetitions"]),
            required_approvers=tuple(value["required_approvers"]),
        )


def sequence_has_loop(
    sequence: tuple[str, ...], *, maximum_steps: int, maximum_tool_repetitions: int
) -> bool:
    if len(sequence) > maximum_steps:
        return True
    return any(sequence.count(tool) > maximum_tool_repetitions for tool in set(sequence))


def _metrics(candidate: AgentCandidate, contract: ReleaseContract) -> dict[str, float]:
    if candidate.requests <= 0:
        raise AIProductionContractError("candidate requests must be greater than zero")
    if not 0 <= candidate.completed_workflows <= candidate.requests:
        raise AIProductionContractError("completed workflows must be between zero and requests")
    looped = sum(
        sequence_has_loop(
            item,
            maximum_steps=contract.maximum_steps,
            maximum_tool_repetitions=contract.maximum_tool_repetitions,
        )
        for item in candidate.tool_sequences
    )
    observed_sequences = len(candidate.tool_sequences)
    loop_rate = 0.0 if observed_sequences == 0 else looped / observed_sequences * 100
    completion_rate = candidate.completed_workflows / candidate.requests * 100
    net_value = (
        candidate.revenue_generated_usd
        + candidate.labor_avoided_usd
        - candidate.inference_cost_usd
        - candidate.retrieval_cost_usd
        - candidate.human_escalation_cost_usd
        - candidate.expected_error_cost_usd
    )
    cost_per_completed = (
        0.0
        if candidate.completed_workflows == 0
        else (candidate.inference_cost_usd + candidate.retrieval_cost_usd)
        / candidate.completed_workflows
    )
    return {
        "completion_rate_pct": round(completion_rate, 2),
        "loop_rate_pct": round(loop_rate, 2),
        "provider_failure_rate_pct": round(candidate.provider_failures / candidate.requests * 100, 2),
        "cost_per_completed_workflow_usd": round(cost_per_completed, 4),
        "net_contribution_value_usd": round(net_value, 2),
    }


def _gate(
    candidate: AgentCandidate,
    metrics: dict[str, float],
    contract: ReleaseContract,
    baseline_metrics: dict[str, float],
    approvals: set[str],
) -> list[str]:
    failures = []
    thresholds = (
        (metrics["completion_rate_pct"], contract.minimum_completion_rate_pct, "completion rate"),
        (candidate.quality_score, contract.minimum_quality_score, "quality score"),
        (candidate.groundedness_score, contract.minimum_groundedness_score, "groundedness score"),
        (
            candidate.correct_tool_rate_pct,
            contract.minimum_correct_tool_rate_pct,
            "correct tool rate",
        ),
    )
    for actual, minimum, label in thresholds:
        if actual < minimum:
            failures.append(f"{label} is below the release minimum")
    upper_bounds = (
        (candidate.p95_latency_ms, contract.maximum_p95_latency_ms, "p95 latency"),
        (metrics["loop_rate_pct"], contract.maximum_loop_rate_pct, "loop rate"),
        (
            metrics["provider_failure_rate_pct"],
            contract.maximum_provider_failure_rate_pct,
            "provider failure rate",
        ),
    )
    for actual, maximum, label in upper_bounds:
        if actual > maximum:
            failures.append(f"{label} exceeds the release maximum")
    if metrics["net_contribution_value_usd"] < baseline_metrics["net_contribution_value_usd"]:
        failures.append("net contribution value regresses from baseline")
    missing = set(contract.required_approvers) - approvals
    if missing:
        failures.append(f"missing required approvals: {sorted(missing)}")
    return failures


def evaluate_ai_release(value: dict, *, approvals: set[str]) -> dict[str, object]:
    contract = ReleaseContract.from_dict(value["release_contract"])
    candidates = [AgentCandidate.from_dict(item) for item in value["candidates"]]
    if not candidates:
        raise AIProductionContractError("at least one candidate is required")
    by_id = {item.candidate_id: item for item in candidates}
    if len(by_id) != len(candidates):
        raise AIProductionContractError("candidate ids must be unique")
    if contract.baseline_candidate_id not in by_id:
        raise AIProductionContractError("baseline candidate is missing")
    candidate_metrics = {item.candidate_id: _metrics(item, contract) for item in candidates}
    baseline_metrics = candidate_metrics[contract.baseline_candidate_id]
    decisions = []
    for candidate in candidates:
        metrics = candidate_metrics[candidate.candidate_id]
        failures = _gate(candidate, metrics, contract, baseline_metrics, approvals)
        decisions.append(
            {
                **asdict(candidate),
                "tool_sequences": [list(item) for item in candidate.tool_sequences],
                "metrics": metrics,
                "decision": "eligible-for-shadow-release" if not failures else "rejected",
                "reasons": failures,
            }
        )
    eligible = [item for item in decisions if item["decision"] == "eligible-for-shadow-release"]
    selected = (
        max(eligible, key=lambda item: item["metrics"]["net_contribution_value_usd"])
        if eligible
        else None
    )
    seed = f"{contract}:{decisions}:{selected and selected['candidate_id']}"
    return {
        "schema_version": "1.0",
        "release_id": value["release_id"],
        "workload_id": value["workload_id"],
        "baseline_candidate_id": contract.baseline_candidate_id,
        "candidate_decisions": decisions,
        "selected_candidate_id": selected["candidate_id"] if selected else None,
        "release_status": "eligible-for-shadow-release" if selected else "blocked",
        "production_promotion_authorized": False,
        "next_stage": "shadow",
        "evidence_sha256": sha256(seed.encode()).hexdigest(),
        "cloud_mutations_executed": 0,
        "limitations": [
            "All observations and business values in the included fixture are synthetic.",
            "Eligibility authorizes only a shadow evaluation, not production promotion.",
            "Production requires canary evidence through the Change Assurance workflow.",
        ],
    }
