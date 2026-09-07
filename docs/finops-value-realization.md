# Azure FinOps value realization

This module connects cloud cost to workload demand and keeps projected savings separate from
financially verified outcomes. It is designed for an MSP operating loop, not as a replacement for
Azure Cost Management, the FinOps toolkit, OpenCost or a customer ledger.

## Decision flow

1. Ingest FOCUS-compatible cost records and workload demand evidence.
2. Attribute direct costs and allocate shared costs through declared usage drivers.
3. Calculate cost per successful business transaction.
4. reject options without reliability, recovery or financial approvals.
5. Send eligible options to Change Assurance for progressive validation.
6. Verify implemented savings against normalized later billing periods.
7. Record only verified net savings in the value ledger.

The included replay stops before steps 5–7. Its empty ledger is intentional: synthetic projected
savings are not customer value evidence.

## Verification contract

Verified net savings must subtract implementation and ongoing operating costs and normalize for
volume, seasonality, negotiated rates, credits, currency, commitment amortization and service-level
changes. A production adapter should preserve source scope, export identity, period, currency,
ingestion timestamp and content hash.

## KPIs

- allocation coverage and unallocated shared cost;
- cost per successful transaction, customer, tenant or inference;
- forecast error and anomaly detection latency;
- commitment coverage and utilization;
- recommendation acceptance and change failure rates;
- estimated-to-verified savings variance;
- verified net savings and savings realization rate;
- post-change SLO, recovery and gross-margin impact.

## Commercial boundary

Indicative delivery ranges are USD 3,000–10,000 for an assessment, USD 10,000–30,000 for
instrumentation and onboarding, and USD 2,000–10,000 monthly. An optional 10–20 percent share of
verified net savings requires an agreed baseline and exclusions. These are positioning ranges, not
quotes or promises of savings.
