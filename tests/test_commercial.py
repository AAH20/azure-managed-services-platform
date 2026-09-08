import pytest

from azure_msp.commercial import CommercialContractError, evaluate_commercial_onboarding


def fixture():
    return {
        "onboarding_id": "onboard",
        "customer_contract": {
            "customer_id": "customer",
            "subscription_id": "sub",
            "current_state": "awaiting-activation",
            "requested_state": "activation-validated",
            "required_region": "westeurope",
            "regulated": True,
            "required_isolation": "dedicated-stamp",
            "monthly_minimum_usd": 1000,
            "forecast_overage_usd": 200,
            "minimum_gross_margin_pct": 40,
            "required_entitlements": ["operations"],
        },
        "control_contract": {"required_approvers": ["owner"], "meter_variance_usd": 0.01},
        "deployment_offers": [
            {
                "offer_id": "offer",
                "isolation": "dedicated-stamp",
                "region": "westeurope",
                "entitlements": ["operations"],
                "infrastructure_cost_usd": 200,
                "model_cost_usd": 50,
                "monitoring_cost_usd": 20,
                "license_cost_usd": 10,
                "support_cost_usd": 100,
                "expected_service_credit_usd": 10,
                "marketplace_fee_usd": 30,
            }
        ],
        "usage_events": [
            {
                "event_id": "one",
                "idempotency_key": "run-one",
                "subscription_id": "sub",
                "meter": "workflow",
                "quantity": 10,
                "unit_price_usd": 2,
                "billing_period": "2026-09",
            }
        ],
        "invoice_evidence": {"billing_period": "2026-09", "metered_amount_usd": 20},
    }


def test_profitable_reconciled_offer_produces_plan_only():
    report = evaluate_commercial_onboarding(fixture(), approvals={"owner"})
    assert report["decision"] == "eligible-for-provisioning-plan"
    assert report["selected_offer_id"] == "offer"
    assert report["offer_results"][0]["projected_gross_margin_pct"] == 65
    assert report["provisioning_executed"] is False
    assert report["entitlements_activated"] == []
    assert report["cloud_mutations_executed"] == 0


def test_duplicate_usage_key_is_not_double_billed():
    value = fixture()
    value["usage_events"].append({**value["usage_events"][0], "event_id": "two"})
    report = evaluate_commercial_onboarding(value, approvals={"owner"})
    usage = report["usage_reconciliation"]
    assert usage["accepted_events"] == 1
    assert usage["duplicate_idempotency_keys"] == ["run-one"]
    assert usage["metered_amount_usd"] == 20


def test_usage_from_another_billing_period_is_not_invoiced():
    value = fixture()
    value["usage_events"].append(
        {**value["usage_events"][0], "event_id": "next-month", "billing_period": "2026-10"}
    )
    report = evaluate_commercial_onboarding(value, approvals={"owner"})
    assert report["usage_reconciliation"]["accepted_events"] == 1
    assert report["usage_reconciliation"]["metered_amount_usd"] == 20


def test_low_margin_and_wrong_isolation_are_rejected():
    value = fixture()
    offer = value["deployment_offers"][0]
    offer.update({"isolation": "shared-stamp", "infrastructure_cost_usd": 1000})
    report = evaluate_commercial_onboarding(value, approvals={"owner"})
    assert report["decision"] == "blocked"
    reasons = report["offer_results"][0]["reasons"]
    assert "offer does not meet the required isolation model" in reasons
    assert "projected gross margin is below the commercial threshold" in reasons


def test_invalid_lifecycle_transition_is_rejected():
    value = fixture()
    value["customer_contract"]["requested_state"] = "active"
    with pytest.raises(CommercialContractError, match="invalid lifecycle transition"):
        evaluate_commercial_onboarding(value, approvals={"owner"})
