import copy

import pytest

from azure_msp.ai_value import AIValueContractError, evaluate_ai_value


def fixture():
    return {
        "evaluation_id": "value",
        "period": "2026-09",
        "shared_platform_cost_usd": 6,
        "rate_cards": [
            {
                "provider": "provider",
                "model": "model",
                "input_per_million_usd": 10,
                "output_per_million_usd": 20,
                "gpu_hour_usd": 0,
                "tool_call_usd": 1,
            }
        ],
        "outcome_events": [
            {
                "event_id": "one",
                "trace_id": "trace-one",
                "tenant_id": "tenant",
                "workflow_id": "workflow",
                "provider": "provider",
                "model": "model",
                "input_tokens": 100000,
                "output_tokens": 50000,
                "tool_calls": 1,
                "attempt": 1,
                "accepted": True,
                "evaluation_passed": True,
                "latency_ms": 1000,
                "business_value_usd": 50,
                "revenue_usd": 20,
            }
        ],
        "routing_contract": {
            "allowed_regions": ["westeurope"],
            "minimum_pass_rate_pct": 95,
            "maximum_p95_latency_ms": 2000,
            "required_approvers": ["owner"],
        },
        "routing_candidates": [
            {
                "candidate_id": "candidate",
                "provider": "provider",
                "model": "model",
                "region": "westeurope",
                "projected_cost_per_accepted_outcome_usd": 5,
                "projected_evaluation_pass_rate_pct": 98,
                "projected_p95_latency_ms": 1500,
                "reversible": True,
            }
        ],
    }


def test_cost_value_and_routing_are_evidence_bound():
    report = evaluate_ai_value(fixture(), approvals={"owner"})
    workflow = report["economics"]["workflows"][0]
    assert report["economics"]["total_cost_usd"] == 9
    assert workflow["cost_per_accepted_outcome_usd"] == 9
    assert workflow["gross_margin_pct"] == 55
    assert report["selected_shadow_candidate_id"] == "candidate"
    assert report["routing_change_executed"] is False
    assert report["verified_savings_ledger"] == []


def test_rejected_retry_is_counted_as_waste():
    value = fixture()
    retry = copy.deepcopy(value["outcome_events"][0])
    retry.update({"event_id": "two", "attempt": 2, "accepted": False})
    value["outcome_events"].append(retry)
    report = evaluate_ai_value(value, approvals={"owner"})
    assert report["economics"]["retry_and_rejected_waste_usd"] > 0


def test_candidate_fails_region_quality_latency_and_approval_contracts():
    value = fixture()
    candidate = value["routing_candidates"][0]
    candidate.update(
        {
            "region": "eastus",
            "projected_evaluation_pass_rate_pct": 80,
            "projected_p95_latency_ms": 3000,
        }
    )
    report = evaluate_ai_value(value, approvals=set())
    assert report["selected_shadow_candidate_id"] is None
    assert len(report["routing_candidates"][0]["reasons"]) == 4


def test_missing_rate_card_is_rejected():
    value = fixture()
    value["outcome_events"][0]["model"] = "unknown"
    with pytest.raises(AIValueContractError, match="missing rate card"):
        evaluate_ai_value(value, approvals={"owner"})
