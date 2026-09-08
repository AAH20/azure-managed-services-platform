import pytest

from azure_msp.data_reliability import DataReliabilityError, downstream_nodes, evaluate_data_release


def fixture():
    return {
        "release_id": "release", "customer_id": "customer",
        "release_contract": {"required_approvers": ["owner"]},
        "data_contracts": [{
            "product_id": "revenue", "owner": "finance", "maximum_age_minutes": 30,
            "minimum_completeness_pct": 99, "maximum_reconciliation_variance_pct": 1,
            "contribution_margin_per_hour_usd": 5000,
            "fields": [{"name": "amount", "data_type": "decimal", "unit": "USD",
                        "nullable": False, "minimum": 0, "maximum": 10000}],
        }],
        "observations": [{
            "product_id": "revenue", "pipeline_status": "succeeded", "age_minutes": 5,
            "completeness_pct": 100, "reconciliation_variance_pct": 0.1,
            "fields": [{"name": "amount", "data_type": "decimal", "unit": "USD",
                        "null_count": 0, "observed_minimum": 10, "observed_maximum": 9000}],
        }],
        "lineage_edges": [{"source": "revenue", "target": "dashboard"},
                          {"source": "dashboard", "target": "agent"}],
    }


def test_valid_product_is_eligible_but_not_published():
    report = evaluate_data_release(fixture(), approvals={"owner"})
    assert report["decision"] == "eligible-for-controlled-publication"
    assert report["publication_authorized"] is False
    assert report["data_mutations_executed"] == 0


def test_semantic_unit_change_blocks_downstream_consumers():
    value = fixture()
    field = value["observations"][0]["fields"][0]
    field.update({"data_type": "integer", "unit": "USD-cents", "observed_maximum": 900000})
    value["observations"][0]["reconciliation_variance_pct"] = 9900
    report = evaluate_data_release(value, approvals={"owner"})
    assert report["decision"] == "blocked"
    assert report["affected_consumers"] == ["agent", "dashboard"]
    failures = report["product_results"][0]["evaluation"]["failures"]
    assert "amount: unit changed from 'USD' to 'USD-cents'" in failures
    assert report["economics"]["contribution_margin_at_risk_per_hour_usd"] == 5000
    assert all(item["authorization"] != "executed" for item in report["remediation_proposals"])


def test_lineage_traversal_is_transitive_and_cycle_safe():
    edges = [("a", "b"), ("b", "c"), ("c", "a")]
    assert downstream_nodes("a", edges) == ["b", "c"]


def test_missing_observation_is_unknown_not_pass():
    value = fixture()
    value["observations"] = []
    with pytest.raises(DataReliabilityError, match="observations are missing"):
        evaluate_data_release(value, approvals={"owner"})
