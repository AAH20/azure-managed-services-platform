import pytest

from azure_msp.ai_production import (
    AIProductionContractError,
    evaluate_ai_release,
    sequence_has_loop,
)


def fixture():
    candidate = {
        "candidate_id": "baseline",
        "provider": "Foundry",
        "model": "router",
        "requests": 100,
        "completed_workflows": 95,
        "quality_score": 0.95,
        "groundedness_score": 0.95,
        "correct_tool_rate_pct": 99,
        "p95_latency_ms": 1000,
        "inference_cost_usd": 100,
        "retrieval_cost_usd": 10,
        "revenue_generated_usd": 1000,
        "labor_avoided_usd": 200,
        "human_escalation_cost_usd": 50,
        "expected_error_cost_usd": 20,
        "provider_failures": 0,
        "tool_sequences": [["retrieve", "respond"], ["retrieve", "respond"]],
    }
    return {
        "release_id": "release",
        "workload_id": "support",
        "release_contract": {
            "baseline_candidate_id": "baseline",
            "minimum_completion_rate_pct": 90,
            "minimum_quality_score": 0.9,
            "minimum_groundedness_score": 0.9,
            "minimum_correct_tool_rate_pct": 97,
            "maximum_p95_latency_ms": 2000,
            "maximum_loop_rate_pct": 10,
            "maximum_provider_failure_rate_pct": 1,
            "maximum_steps": 5,
            "maximum_tool_repetitions": 2,
            "required_approvers": ["owner"],
        },
        "candidates": [candidate],
    }


def test_eligible_release_stops_at_shadow():
    report = evaluate_ai_release(fixture(), approvals={"owner"})
    assert report["selected_candidate_id"] == "baseline"
    assert report["release_status"] == "eligible-for-shadow-release"
    assert report["production_promotion_authorized"] is False
    assert report["candidate_decisions"][0]["metrics"]["net_contribution_value_usd"] == 1020
    assert report["cloud_mutations_executed"] == 0


def test_cheaper_candidate_is_rejected_when_outcomes_regress():
    value = fixture()
    cheap = {**value["candidates"][0], "candidate_id": "cheap", "inference_cost_usd": 1}
    cheap.update({"quality_score": 0.7, "completed_workflows": 70, "revenue_generated_usd": 500})
    value["candidates"].append(cheap)
    report = evaluate_ai_release(value, approvals={"owner"})
    decision = next(item for item in report["candidate_decisions"] if item["candidate_id"] == "cheap")
    assert decision["decision"] == "rejected"
    assert "quality score is below the release minimum" in decision["reasons"]
    assert "net contribution value regresses from baseline" in decision["reasons"]


def test_repeated_tool_and_step_budget_detect_loops():
    assert sequence_has_loop(("retrieve", "retrieve", "retrieve"), maximum_steps=5, maximum_tool_repetitions=2)
    assert sequence_has_loop(("a", "b", "c"), maximum_steps=2, maximum_tool_repetitions=2)
    assert not sequence_has_loop(("retrieve", "respond"), maximum_steps=5, maximum_tool_repetitions=2)


def test_missing_baseline_is_rejected():
    value = fixture()
    value["release_contract"]["baseline_candidate_id"] = "missing"
    with pytest.raises(AIProductionContractError, match="baseline"):
        evaluate_ai_release(value, approvals={"owner"})
