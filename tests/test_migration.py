import pytest

from azure_msp.migration import (
    MigrationAsset,
    MigrationContractError,
    dependency_order,
    evaluate_migration,
)


def fixture():
    candidate = {
        "strategy": "replatform",
        "target": "Azure App Service",
        "ready": True,
        "annual_target_cost_usd": 100,
        "implementation_cost_usd": 20,
        "annual_operational_saving_usd": 30,
        "annual_revenue_enablement_usd": 40,
        "expected_downtime_loss_usd": 5,
        "risk_reserve_usd": 5,
    }
    evidence = {
        "test_migration_passed": True,
        "network_verified": True,
        "identity_verified": True,
        "dns_verified": True,
        "backup_verified": True,
        "rollback_verified": True,
        "transaction_verified": True,
        "observed_downtime_minutes": 10,
        "observed_rpo_minutes": 2,
    }
    return {
        "migration_id": "m-1",
        "customer_id": "customer",
        "cutover_contract": {
            "maximum_downtime_minutes": 30,
            "maximum_rpo_minutes": 15,
            "required_approvers": ["owner"],
        },
        "assets": [
            {"asset_id": "db", "name": "DB", "source_platform": "VMware", "annual_source_cost_usd": 120, "dependencies": [], "candidates": [candidate]},
            {"asset_id": "api", "name": "API", "source_platform": "VMware", "annual_source_cost_usd": 120, "dependencies": ["db"], "candidates": [candidate]},
        ],
        "cutover_evidence": [
            {"asset_id": "db", **evidence},
            {"asset_id": "api", **evidence},
        ],
    }


def test_dependency_order_and_eligible_wave():
    report = evaluate_migration(fixture(), approvals={"owner"})
    assert report["dependency_order"] == ["db", "api"]
    assert report["wave_status"] == "eligible-for-controlled-cutover"
    assert report["assessments"][0]["recommended_candidate"]["first_year_net_value_usd"] == 60
    assert report["source_decommission"]["approved"] is False
    assert report["cloud_mutations_executed"] == 0


def test_failed_dns_and_transaction_block_wave():
    value = fixture()
    value["cutover_evidence"][1]["dns_verified"] = False
    value["cutover_evidence"][1]["transaction_verified"] = False
    report = evaluate_migration(value, approvals={"owner"})
    assert report["wave_status"] == "blocked"
    assert report["cutover_decisions"]["api"]["reasons"] == [
        "dns verification failed",
        "business_transaction verification failed",
    ]


def test_missing_approval_blocks_every_asset():
    report = evaluate_migration(fixture(), approvals=set())
    assert all(item["decision"] == "blocked" for item in report["cutover_decisions"].values())


def test_cycle_is_rejected():
    value = fixture()
    value["assets"][0]["dependencies"] = ["api"]
    assets = [MigrationAsset.from_dict(item) for item in value["assets"]]
    with pytest.raises(MigrationContractError, match="cycle"):
        dependency_order(assets)
