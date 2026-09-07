import pytest

from azure_msp.evaluator import evaluate, proposals
from azure_msp.models import Customer, Resource
from azure_msp.operations import (
    InvalidTransitionError,
    TenantBoundaryError,
    build_work_queue,
    build_workloads,
    transition,
)


def setup_queue():
    customer = Customer("acme", "Acme", "tenant-acme")
    resource = Resource(
        id="/subscriptions/test/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
        name="vm1",
        type="microsoft.compute/virtualmachines",
        location="eastus",
        tags={
            "owner": "ops",
            "environment": "prod",
            "costCenter": "1",
            "workload": "payments",
        },
        properties={
            "monitoringEnabled": False,
            "backupEnabled": True,
            "daysSincePatchAssessment": 1,
        },
    )
    findings = evaluate([resource])
    workloads = build_workloads(
        customer,
        [resource],
        [
            {
                "workload_id": "payments",
                "name": "Payments",
                "owner": "ops",
                "criticality": "mission-critical",
                "monthly_revenue_usd": 500000,
            }
        ],
    )
    return build_work_queue(customer, workloads, findings, proposals(findings))[0]


def test_priority_includes_business_exposure():
    item = setup_queue()
    assert item.priority_score > 50
    assert item.estimated_monthly_exposure_usd > 0


def test_approval_is_audited():
    item = setup_queue()
    event = transition(
        item,
        authenticated_customer_id="acme",
        action="approve",
        actor="owner@acme.invalid",
        reason="Maintenance window confirmed",
    )
    assert item.status == "approved"
    assert event.previous_status == "proposed"


def test_cross_tenant_action_is_rejected():
    with pytest.raises(TenantBoundaryError):
        transition(
            setup_queue(),
            authenticated_customer_id="other",
            action="approve",
            actor="other",
            reason="not authorized",
        )


def test_invalid_transition_is_rejected():
    with pytest.raises(InvalidTransitionError):
        transition(
            setup_queue(),
            authenticated_customer_id="acme",
            action="validate",
            actor="owner",
            reason="attempted to skip approval",
        )
