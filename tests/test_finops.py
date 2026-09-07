import pytest

from azure_msp.finops import FinOpsContractError, evaluate_value_realization


def evidence():
    return {
        "evaluation_id": "f-1",
        "customer_id": "customer",
        "cost_records": [
            {"period": "2026-08", "resource_id": "db", "service": "DB", "cost_usd": 800, "allocation": "checkout"},
            {"period": "2026-08", "resource_id": "monitor", "service": "Monitor", "cost_usd": 200, "allocation": "shared", "usage_driver": "requests"},
            {"period": "2026-08", "resource_id": "unknown", "service": "Network", "cost_usd": 100, "allocation": "unmapped"},
        ],
        "workload_demand": [
            {"period": "2026-08", "workload_id": "checkout", "successful_transactions": 10000, "allocation_drivers": {"requests": 75}},
            {"period": "2026-08", "workload_id": "catalog", "successful_transactions": 20000, "allocation_drivers": {"requests": 25}},
        ],
        "decision_contract": {"required_approvers": ["owner"]},
        "optimization_options": [
            {"option_id": "safe", "title": "Safe", "expected_monthly_saving_usd": 100, "implementation_cost_usd": 200, "reliability_verified": True, "recovery_verified": True, "reversible": True, "commitment_months": 0},
            {"option_id": "unsafe", "title": "Unsafe", "expected_monthly_saving_usd": 500, "implementation_cost_usd": 0, "reliability_verified": False, "recovery_verified": True, "reversible": True, "commitment_months": 0},
        ],
    }


def test_allocates_shared_cost_and_keeps_savings_unverified():
    report = evaluate_value_realization(evidence(), approvals={"owner"})
    checkout = report["economics"]["workloads"]["checkout"]
    assert checkout["allocated_cost_usd"] == 950
    assert checkout["cost_per_successful_transaction_usd"] == 0.095
    assert report["economics"]["unallocated_cost_usd"] == 100
    assert report["economics"]["allocation_coverage_pct"] == 90.91
    assert report["optimization_decisions"][0]["decision"] == "eligible-for-change-assurance"
    assert report["optimization_decisions"][1]["decision"] == "rejected"
    assert report["verified_savings_ledger"] == []
    assert report["cloud_mutations_executed"] == 0


def test_missing_approval_rejects_option():
    report = evaluate_value_realization(evidence(), approvals=set())
    assert all(item["decision"] == "rejected" for item in report["optimization_decisions"])


def test_mixed_periods_are_rejected():
    value = evidence()
    value["workload_demand"][0]["period"] = "2026-07"
    with pytest.raises(FinOpsContractError, match="one evaluation period"):
        evaluate_value_realization(value, approvals={"owner"})


def test_zero_transactions_are_rejected():
    value = evidence()
    value["workload_demand"][0]["successful_transactions"] = 0
    with pytest.raises(FinOpsContractError, match="greater than zero"):
        evaluate_value_realization(value, approvals={"owner"})
