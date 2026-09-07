import pytest

from azure_msp.change_assurance import (
    ChangeContract,
    ChangeContractError,
    RingObservation,
    evaluate_rollout,
    validate_contract,
)


def contract(**risk_overrides):
    risk = {
        "production": True,
        "monitoring_verified": True,
        "recovery_verified": True,
        "rollback_steps": ["revert"],
    }
    risk.update(risk_overrides)
    return ChangeContract.from_dict(
        {
            "change_id": "pr-1",
            "customer_id": "customer",
            "workload_id": "checkout",
            "intent": {"expected_monthly_saving_usd": 1000},
            "risk": risk,
            "deployment": {"rings": ["test", "canary", "production"]},
            "approval": {"required": ["owner", "operations"]},
            "validation": {
                "maximum_p95_latency_ms": 250,
                "maximum_error_rate_pct": 0.5,
                "minimum_transaction_success_pct": 99.5,
            },
            "operations": [
                {
                    "resource_id": "/subscriptions/test/database",
                    "action": "update",
                    "before": {"sku": "large"},
                    "after": {"sku": "small"},
                }
            ],
        }
    )


def observation(ring, latency=100, errors=0.1, success=100):
    return RingObservation(ring, latency, errors, success, 500)


def test_canary_regression_halts_and_rejects_saving():
    report = evaluate_rollout(
        contract(),
        [observation("test"), observation("canary", 400, 2, 97)],
        approvals={"owner", "operations"},
    )
    assert report["outcome"] == "halted"
    assert report["halted_at"] == "canary"
    assert report["promoted_rings"] == ["test"]
    assert report["economics"]["realized_monthly_saving_usd"] == 0
    assert report["economics"]["saving_verified"] is False
    assert report["rollback"]["required"] is True
    assert report["cloud_mutations_executed"] == 0


def test_missing_monitoring_is_rejected():
    with pytest.raises(ChangeContractError, match="monitoring"):
        validate_contract(contract(monitoring_verified=False), {"owner", "operations"})


def test_missing_recovery_is_rejected():
    with pytest.raises(ChangeContractError, match="recovery"):
        validate_contract(contract(recovery_verified=False), {"owner", "operations"})


def test_missing_ring_observation_halts():
    report = evaluate_rollout(
        contract(),
        [observation("test")],
        approvals={"owner", "operations"},
    )
    assert report["halted_at"] == "canary"
    assert report["decisions"][-1]["reasons"] == ["mandatory observation is missing"]


def test_delete_requires_separate_contract():
    value = contract()
    delete_operation = value.operations[0].__class__(
        value.operations[0].resource_id,
        "delete",
        value.operations[0].before,
        {},
    )
    unsafe = ChangeContract(**{**value.__dict__, "operations": (delete_operation,)})
    with pytest.raises(ChangeContractError, match="destructive"):
        validate_contract(unsafe, {"owner", "operations"})
