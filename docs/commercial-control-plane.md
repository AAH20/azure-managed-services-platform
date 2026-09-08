# Azure SaaS control plane and Marketplace revenue factory

This module joins commercial contracts, tenant isolation, entitlements, deployment economics,
usage metering and invoice reconciliation. It complements Azure Marketplace fulfillment APIs and
the community-supported SaaS Accelerator rather than recreating payment or Marketplace services.

## Commercial loop

1. Validate the requested tenant lifecycle transition.
2. Compare shared, dedicated or customer-owned deployment offers.
3. Enforce region, isolation, entitlement and gross-margin contracts.
4. Deduplicate immutable usage events using event and idempotency keys.
5. Reconcile metered usage with invoice evidence.
6. Select an eligible offer for a provisioning plan only.
7. Route infrastructure through the existing assurance modules before activation.

The synthetic regulated EU customer requires a dedicated West Europe stamp and four managed
services. The shared and US offers fail contractual requirements. The profitable dedicated offer
is selected, while duplicate usage is rejected and the invoice reconciles. No entitlement,
lifecycle transition, billing submission or cloud mutation is executed.

## Production adapters

- Microsoft Commercial Marketplace SaaS Fulfillment and Metering APIs
- Azure SaaS Accelerator, Partner Center and customer-owned subscriptions
- Azure Lighthouse and deployment stamps through Bicep/Terraform
- Microsoft Entra external identity, enterprise federation and directory provisioning
- Stripe, OpenMeter or Lago for direct-channel usage and billing
- Existing network, recovery, capacity, data, AI, FinOps and change modules

## KPI and commercial boundary

Track activation time, provisioning success, MRR, expansion, gross margin by tenant, unbilled
usage, meter-to-invoice variance, orphaned resources, upgrade drift and decommission completion.
Indicative ranges are USD 5,000–15,000 for readiness, USD 30,000–150,000 for control-plane
implementation, USD 15,000–60,000 for Marketplace integration and USD 5,000–25,000 monthly for
managed commercial operations. These are positioning ranges, not quotes or guaranteed outcomes.
