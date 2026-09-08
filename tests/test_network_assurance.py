import pytest

from azure_msp.network_assurance import NetworkAssuranceError, evaluate_network_change


def fixture():
    intent = {
        "intent_id": "app-db", "business_service": "orders", "source": "app",
        "destination": "db", "protocol": "tcp", "port": 5432,
        "must_be_reachable": True, "required_transit": ["firewall"],
        "prohibited_transit": ["internet"], "expected_dns_scope": "private",
        "maximum_latency_ms": 20, "contribution_margin_per_hour_usd": 5000,
    }
    path = {
        "intent_id": "app-db", "reachable": True, "return_reachable": True,
        "path": ["app", "firewall", "db"], "resolved_address": "10.0.0.5",
        "dns_scope": "private", "latency_ms": 10, "blocking_control": None,
    }
    return {
        "change_id": "change", "customer_id": "customer",
        "change_contract": {"required_approvers": ["owner"]},
        "connectivity_intents": [intent],
        "path_observations": [{"snapshot": "current", **path},
                              {"snapshot": "proposed", **path}],
    }


def test_unchanged_valid_path_is_eligible_but_not_deployed():
    report = evaluate_network_change(fixture(), approvals={"owner"})
    assert report["decision"] == "eligible-for-change-assurance"
    assert report["production_deployment_authorized"] is False
    assert report["cloud_mutations_executed"] == 0


def test_route_and_dns_regression_blocks_change_and_values_exposure():
    value = fixture()
    value["path_observations"][1].update({
        "reachable": False, "return_reachable": False, "path": ["wrong-next-hop"],
        "dns_scope": "public", "blocking_control": "route-table/bad-route",
    })
    report = evaluate_network_change(value, approvals={"owner"})
    assert report["decision"] == "blocked"
    assert report["comparisons"][0]["behavior_regression"] is True
    assert report["economics"]["contribution_margin_at_risk_per_hour_usd"] == 5000
    failures = report["comparisons"][0]["proposed"]["failures"]
    assert "flow must remain reachable" in failures
    assert "DNS scope 'public' differs from 'private'" in failures


def test_prohibited_flow_becoming_reachable_is_blocked():
    value = fixture()
    value["connectivity_intents"][0].update(
        {"must_be_reachable": False, "expected_dns_scope": "not-applicable"}
    )
    value["path_observations"][0].update({"reachable": False, "return_reachable": False})
    assert evaluate_network_change(value, approvals={"owner"})["decision"] == "blocked"


def test_missing_snapshot_is_rejected_as_unknown():
    value = fixture()
    value["path_observations"].pop()
    with pytest.raises(NetworkAssuranceError, match="missing snapshots"):
        evaluate_network_change(value, approvals={"owner"})
