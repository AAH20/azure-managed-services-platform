import pytest

from azure_msp.recovery import (
    DrillEvent,
    RecoveryContract,
    RecoveryContractError,
    evaluate_drill,
    recovery_order,
    validate_drill_authority,
)


def contract():
    return RecoveryContract.from_dict(
        {
            "customer_id": "customer",
            "workload_id": "checkout",
            "monthly_revenue_usd": 100000,
            "objectives": {"rto_minutes": 20, "rpo_minutes": 5},
            "approval": {
                "isolated_network_required": True,
                "production_failover_allowed": False,
                "required_approvers": ["owner", "operations"],
            },
            "components": [
                {
                    "component_id": "database",
                    "kind": "data",
                    "depends_on": [],
                    "validation": ["database_consistency"],
                },
                {
                    "component_id": "api",
                    "kind": "compute",
                    "depends_on": ["database"],
                    "validation": ["synthetic_transaction"],
                },
            ],
        }
    )


def test_dependency_order_is_deterministic():
    assert recovery_order(contract()) == ["database", "api"]


def test_production_failover_is_rejected():
    with pytest.raises(RecoveryContractError, match="production failover"):
        validate_drill_authority(
            contract(),
            isolated_network=True,
            requested_production_failover=True,
            approvals={"owner", "operations"},
        )


def test_missing_approval_is_rejected():
    with pytest.raises(RecoveryContractError, match="operations"):
        validate_drill_authority(
            contract(),
            isolated_network=True,
            requested_production_failover=False,
            approvals={"owner"},
        )


def test_drill_detects_rto_rpo_and_transaction_failures():
    events = [
        DrillEvent("database", 0, 8, 9, {"database_consistency": True}, 2.0),
        DrillEvent("api", 8, 25, 0, {"synthetic_transaction": False}, 1.0),
    ]
    report = evaluate_drill(contract(), events)
    assert report["outcome"] == "failed"
    assert report["observed"]["actual_rto_minutes"] == 25
    assert report["observed"]["actual_rpo_minutes"] == 9
    objectives = {finding["objective"] for finding in report["findings"]}
    assert objectives == {"synthetic_transaction", "RTO", "RPO"}
    assert report["cloud_mutations_executed"] == 0


def test_cycle_is_rejected():
    value = contract()
    cyclic = RecoveryContract(
        **{
            **value.__dict__,
            "components": (
                value.components[0].__class__("database", "data", ("api",), ()),
                value.components[1].__class__("api", "compute", ("database",), ()),
            ),
        }
    )
    with pytest.raises(RecoveryContractError, match="cycle"):
        recovery_order(cyclic)
