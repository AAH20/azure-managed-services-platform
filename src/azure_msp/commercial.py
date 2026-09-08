from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class CommercialContractError(ValueError):
    """Raised when commercial, tenant or metering evidence is invalid."""


LIFECYCLE_TRANSITIONS = {
    "contracted": {"awaiting-activation"},
    "awaiting-activation": {"activation-validated"},
    "activation-validated": {"provisioning"},
    "provisioning": {"validating"},
    "validating": {"active"},
    "active": {"suspended", "cancellation-pending"},
    "suspended": {"active", "cancellation-pending"},
    "cancellation-pending": {"retention-period"},
    "retention-period": {"decommission-approved"},
    "decommission-approved": {"decommissioned"},
}


@dataclass(frozen=True)
class CustomerContract:
    customer_id: str
    subscription_id: str
    current_state: str
    requested_state: str
    required_region: str
    regulated: bool
    required_isolation: str
    monthly_minimum_usd: float
    forecast_overage_usd: float
    minimum_gross_margin_pct: float
    required_entitlements: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict) -> CustomerContract:
        isolation = value["required_isolation"]
        if isolation not in {"shared-stamp", "dedicated-stamp", "customer-owned-subscription"}:
            raise CommercialContractError(f"unsupported isolation model: {isolation}")
        return cls(
            customer_id=value["customer_id"],
            subscription_id=value["subscription_id"],
            current_state=value["current_state"],
            requested_state=value["requested_state"],
            required_region=value["required_region"],
            regulated=bool(value["regulated"]),
            required_isolation=isolation,
            monthly_minimum_usd=float(value["monthly_minimum_usd"]),
            forecast_overage_usd=float(value["forecast_overage_usd"]),
            minimum_gross_margin_pct=float(value["minimum_gross_margin_pct"]),
            required_entitlements=tuple(value["required_entitlements"]),
        )


@dataclass(frozen=True)
class DeploymentOffer:
    offer_id: str
    isolation: str
    region: str
    entitlements: tuple[str, ...]
    infrastructure_cost_usd: float
    model_cost_usd: float
    monitoring_cost_usd: float
    license_cost_usd: float
    support_cost_usd: float
    expected_service_credit_usd: float
    marketplace_fee_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> DeploymentOffer:
        return cls(
            offer_id=value["offer_id"], isolation=value["isolation"], region=value["region"],
            entitlements=tuple(value["entitlements"]),
            infrastructure_cost_usd=float(value["infrastructure_cost_usd"]),
            model_cost_usd=float(value["model_cost_usd"]),
            monitoring_cost_usd=float(value["monitoring_cost_usd"]),
            license_cost_usd=float(value["license_cost_usd"]),
            support_cost_usd=float(value["support_cost_usd"]),
            expected_service_credit_usd=float(value["expected_service_credit_usd"]),
            marketplace_fee_usd=float(value["marketplace_fee_usd"]),
        )


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    idempotency_key: str
    subscription_id: str
    meter: str
    quantity: float
    unit_price_usd: float
    billing_period: str

    @classmethod
    def from_dict(cls, value: dict) -> UsageEvent:
        quantity = float(value["quantity"])
        if quantity < 0:
            raise CommercialContractError("usage quantity cannot be negative")
        return cls(
            event_id=value["event_id"], idempotency_key=value["idempotency_key"],
            subscription_id=value["subscription_id"], meter=value["meter"], quantity=quantity,
            unit_price_usd=float(value["unit_price_usd"]), billing_period=value["billing_period"],
        )


def validate_transition(current: str, requested: str) -> bool:
    return requested in LIFECYCLE_TRANSITIONS.get(current, set())


def aggregate_usage(
    events: list[UsageEvent], subscription_id: str, billing_period: str
) -> dict[str, object]:
    accepted, duplicate_ids, duplicate_keys = [], [], []
    seen_ids, seen_keys = set(), set()
    for event in events:
        if event.subscription_id != subscription_id or event.billing_period != billing_period:
            continue
        if event.event_id in seen_ids:
            duplicate_ids.append(event.event_id)
            continue
        if event.idempotency_key in seen_keys:
            duplicate_keys.append(event.idempotency_key)
            continue
        seen_ids.add(event.event_id)
        seen_keys.add(event.idempotency_key)
        accepted.append(event)
    amount = sum(item.quantity * item.unit_price_usd for item in accepted)
    return {
        "accepted_events": len(accepted), "duplicate_event_ids": sorted(set(duplicate_ids)),
        "duplicate_idempotency_keys": sorted(set(duplicate_keys)),
        "metered_amount_usd": round(amount, 2),
    }


def evaluate_offer(
    contract: CustomerContract,
    offer: DeploymentOffer,
    required_approvals: set[str],
    approvals: set[str],
) -> dict[str, object]:
    revenue = contract.monthly_minimum_usd + contract.forecast_overage_usd
    cost = sum(
        (
            offer.infrastructure_cost_usd,
            offer.model_cost_usd,
            offer.monitoring_cost_usd,
            offer.license_cost_usd,
            offer.support_cost_usd,
            offer.expected_service_credit_usd,
            offer.marketplace_fee_usd,
        )
    )
    contribution = revenue - cost
    margin = 0.0 if revenue == 0 else contribution / revenue * 100
    failures = []
    if offer.isolation != contract.required_isolation:
        failures.append("offer does not meet the required isolation model")
    if offer.region != contract.required_region:
        failures.append("offer does not meet the required data region")
    missing_entitlements = set(contract.required_entitlements) - set(offer.entitlements)
    if missing_entitlements:
        failures.append(f"missing entitlements: {sorted(missing_entitlements)}")
    if margin < contract.minimum_gross_margin_pct:
        failures.append("projected gross margin is below the commercial threshold")
    missing_approvals = required_approvals - approvals
    if missing_approvals:
        failures.append(f"missing required approvals: {sorted(missing_approvals)}")
    return {
        "offer": asdict(offer),
        "projected_revenue_usd": round(revenue, 2),
        "projected_cost_usd": round(cost, 2),
        "projected_contribution_usd": round(contribution, 2),
        "projected_gross_margin_pct": round(margin, 2),
        "decision": "eligible-for-provisioning-plan" if not failures else "rejected",
        "reasons": failures,
    }


def evaluate_commercial_onboarding(value: dict, *, approvals: set[str]) -> dict[str, object]:
    contract = CustomerContract.from_dict(value["customer_contract"])
    if not validate_transition(contract.current_state, contract.requested_state):
        raise CommercialContractError(
            f"invalid lifecycle transition: {contract.current_state} -> {contract.requested_state}"
        )
    required_approvals = set(value["control_contract"]["required_approvers"])
    offers = [DeploymentOffer.from_dict(item) for item in value["deployment_offers"]]
    if not offers:
        raise CommercialContractError("at least one deployment offer is required")
    offer_results = [evaluate_offer(contract, offer, required_approvals, approvals) for offer in offers]
    eligible = [item for item in offer_results if item["decision"] == "eligible-for-provisioning-plan"]
    selected = max(eligible, key=lambda item: item["projected_contribution_usd"]) if eligible else None
    billing_period = value["invoice_evidence"]["billing_period"]
    usage = aggregate_usage(
        [UsageEvent.from_dict(item) for item in value["usage_events"]],
        contract.subscription_id,
        billing_period,
    )
    invoice_amount = float(value["invoice_evidence"]["metered_amount_usd"])
    variance = round(invoice_amount - usage["metered_amount_usd"], 2)
    metering_reconciled = abs(variance) <= float(value["control_contract"]["meter_variance_usd"])
    blockers = []
    if selected is None:
        blockers.append("no deployment offer meets commercial and technical contracts")
    if not metering_reconciled:
        blockers.append("metered usage does not reconcile with invoice evidence")
    decision = "eligible-for-provisioning-plan" if not blockers else "blocked"
    seed = f"{contract}:{offer_results}:{usage}:{variance}:{decision}"
    return {
        "schema_version": "1.0",
        "onboarding_id": value["onboarding_id"],
        "customer_contract": asdict(contract),
        "offer_results": offer_results,
        "selected_offer_id": selected["offer"]["offer_id"] if selected else None,
        "usage_reconciliation": {
            **usage,
            "billing_period": billing_period,
            "invoice_metered_amount_usd": invoice_amount,
            "variance_usd": variance,
            "reconciled": metering_reconciled,
        },
        "decision": decision,
        "blockers": blockers,
        "requested_lifecycle_transition": f"{contract.current_state}->{contract.requested_state}",
        "lifecycle_transition_executed": False,
        "entitlements_activated": [],
        "provisioning_executed": False,
        "billing_submissions_executed": 0,
        "evidence_sha256": sha256(seed.encode()).hexdigest(),
        "cloud_mutations_executed": 0,
        "limitations": [
            "The customer, subscription, costs, usage and invoice evidence are synthetic.",
            "Eligibility creates a provisioning plan; it does not activate or provision a tenant.",
            "Marketplace fulfillment and metering APIs are not called by this replay.",
        ],
    }
