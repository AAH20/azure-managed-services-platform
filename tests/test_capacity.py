import pytest

from azure_msp.capacity import CapacityContractError, evaluate_capacity_plan


def fixture():
    dependency = {"dependency_id": "app", "capacity_units_per_second": 120,
                  "ready_in_minutes": 10, "quota_available": True, "load_test_passed": True}
    return {
        "plan_id": "plan", "workload_id": "checkout",
        "forecast": {"peak_units_per_second": 90, "upper_bound_units_per_second": 100,
                     "horizon_minutes": 30, "source_age_minutes": 5,
                     "maximum_source_age_minutes": 15, "confidence_pct": 90,
                     "minimum_confidence_pct": 80, "contribution_margin_per_unit_usd": 2},
        "required_dependencies": ["app"],
        "capacity_contract": {"required_approvers": ["owner"]},
        "portfolios": [{"portfolio_id": "safe", "title": "Safe", "event_cost_usd": 100,
                        "implementation_cost_usd": 50, "expected_abandonment_loss_usd": 20,
                        "expected_revenue_protected_usd": 1000, "reversible": True,
                        "dependencies": [dependency]}],
    }


def test_complete_portfolio_advances_only_to_load_test():
    report = evaluate_capacity_plan(fixture(), approvals={"owner"})
    assert report["decision"] == "eligible-for-load-test"
    assert report["selected_portfolio_id"] == "safe"
    assert report["portfolio_results"][0]["net_event_value_usd"] == 830
    assert report["production_scaling_authorized"] is False
    assert report["cloud_mutations_executed"] == 0


def test_weakest_dependency_blocks_scale_plan():
    value = fixture()
    value["portfolios"][0]["dependencies"][0]["capacity_units_per_second"] = 80
    report = evaluate_capacity_plan(value, approvals={"owner"})
    assert report["decision"] == "blocked"
    assert "app: capacity is below forecast upper bound" in report["portfolio_results"][0]["reasons"]


def test_stale_forecast_and_missing_approval_reject_plan():
    value = fixture()
    value["forecast"]["source_age_minutes"] = 60
    report = evaluate_capacity_plan(value, approvals=set())
    reasons = report["portfolio_results"][0]["reasons"]
    assert "forecast source is stale" in reasons
    assert any("missing required approvals" in item for item in reasons)


def test_invalid_forecast_bounds_are_rejected():
    value = fixture()
    value["forecast"]["upper_bound_units_per_second"] = 50
    with pytest.raises(CapacityContractError, match="upper bound"):
        evaluate_capacity_plan(value, approvals={"owner"})
