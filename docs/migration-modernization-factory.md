# Azure cloud migration and application modernization factory

This module turns discovered assets, dependencies, migration candidates and cutover observations
into a deterministic migration-wave decision. It complements Azure Migrate; it does not recreate
discovery, replication or application assessment.

## Operating loop

1. Import Azure Migrate, Azure Arc, CMDB, vCenter and application evidence.
2. Build a dependency graph and reject unknown dependencies or cycles.
3. Compare retain, retire, rehost, relocate, replatform and refactor candidates.
4. Calculate transparent first-year value using source cost, target cost, implementation cost,
   operating improvement, revenue enablement, downtime exposure and risk reserve.
5. Validate test migration, network, identity, DNS, backup, rollback and business transactions.
6. Route an eligible wave to controlled cutover after named approvals.
7. Verify the target against performance, recovery, security and cost baselines.
8. Decommission the source only through a separate approval contract.

The synthetic replay deliberately fails DNS and checkout transaction validation for the API. The
entire wave is blocked even though the other components pass. This demonstrates dependency-aware
failure handling, not a successful live migration.

## Production adapters

- Azure Migrate discovery, assessment and business-case exports
- vCenter, Hyper-V, Azure Arc and CMDB inventory
- Azure Resource Graph and landing-zone readiness
- Terraform/OpenTofu and Bicep plan evidence
- Azure Monitor and OpenTelemetry business transactions
- Azure Site Recovery, Database Migration Service and application migration tools
- Cost Management/FOCUS data through the FinOps value-realization module

## KPIs

- discovery coverage and dependency confidence;
- migration readiness and blockers by owner;
- workloads migrated per wave and first-pass success;
- cutover duration, rollback rate and schedule variance;
- post-cutover availability, latency, incidents, RTO and RPO;
- forecast versus actual Azure cost and dual-running cost;
- source decommission completion and verified net value.

## Commercial boundary

Indicative positioning ranges are USD 7,500–25,000 for discovery and business case, USD
15,000–60,000 for the migration foundation, USD 10,000–50,000 per wave and USD 20,000–150,000+
for application modernization. Scope, estate size, downtime, licenses and third-party consumption
determine an actual quote. The repository makes no claim of customer deployments or guaranteed
migration outcomes.
