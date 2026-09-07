import pytest

from azure_msp.incident import (
    DiagnosticEvidence,
    IncidentContract,
    IncidentContractError,
    IncidentSignal,
    RestorationObservation,
    authorize_action,
    correlate_signals,
    evaluate_incident,
)


def contract():
    return IncidentContract.from_dict(
        {
            "incident_id": "inc-1",
            "customer_id": "customer",
            "workload_id": "checkout",
            "linked_change_id": "pr-1",
            "business_context": {
                "normal_transactions_per_minute": 10,
                "contribution_margin_per_transaction_usd": 5,
            },
            "objectives": {"restore_minutes": 20},
            "restoration_health": {
                "maximum_p95_latency_ms": 250,
                "maximum_error_rate_pct": 0.5,
                "minimum_transaction_success_pct": 99.5,
            },
            "authority": {
                "automatic": ["query_logs"],
                "approval_required": ["rollback_deployment"],
                "prohibited": ["delete_resource"],
                "required_approvers": ["owner"],
            },
        }
    )


def signals():
    return [
        IncidentSignal("one", "checkout", "azure", "latency", 400, 250, 0),
        IncidentSignal("two", "checkout", "prometheus", "latency", 390, 250, 1),
    ]


def test_signal_deduplication_preserves_sources():
    result = correlate_signals(contract(), signals())
    assert result["raw_signals"] == 2
    assert result["deduplicated_signals"] == 1
    assert result["normalized_symptoms"][0]["sources"] == ["azure", "prometheus"]


def test_mutating_action_requires_approval():
    with pytest.raises(IncidentContractError, match="owner"):
        authorize_action(contract(), "rollback_deployment", set())


def test_prohibited_action_is_never_authorized():
    with pytest.raises(IncidentContractError, match="prohibited"):
        authorize_action(contract(), "delete_resource", {"owner"})


def test_restoration_is_transaction_verified():
    report = evaluate_incident(
        contract(),
        signals(),
        [
            DiagnosticEvidence(
                "metrics",
                "read",
                "saturation",
                "capacity exhausted",
                True,
                "controlled_intervention",
            )
        ],
        RestorationObservation(12, 180, 0.1, 99.9),
        proposed_action="rollback_deployment",
        approvals={"owner"},
    )
    assert report["restoration"]["service_restored"] is True
    assert report["restoration"]["restore_objective_met"] is True
    assert report["economics"]["estimated_contribution_margin_exposure_usd"] == 600
    assert report["economics"]["verified_financial_loss_usd"] is None
    assert report["cloud_mutations_executed"] == 0


def test_unproven_cause_cannot_be_marked_verified():
    with pytest.raises(IncidentContractError, match="controlled_intervention"):
        evaluate_incident(
            contract(),
            signals(),
            [DiagnosticEvidence("metrics", "read", "correlated", "capacity", True, None)],
            RestorationObservation(12, 180, 0.1, 99.9),
            proposed_action="rollback_deployment",
            approvals={"owner"},
        )


def test_unhealthy_transaction_prevents_restoration_claim():
    report = evaluate_incident(
        contract(),
        signals(),
        [],
        RestorationObservation(12, 180, 0.1, 95),
        proposed_action="rollback_deployment",
        approvals={"owner"},
    )
    assert report["restoration"]["service_restored"] is False
    assert "synthetic_transaction" in report["restoration"]["health_failures"]
