# Azure Recovery Assurance Service

Recovery Assurance evaluates whether a business workload can recover within its declared RTO and
RPO. It distinguishes infrastructure restoration from application recovery and successful user
transactions.

## Evidence progression

```text
Backup configured
  -> recovery point available
  -> component restored
  -> dependencies recovered in order
  -> infrastructure assertions pass
  -> application assertions pass
  -> synthetic transaction succeeds
  -> RTO and RPO achieved
  -> cleanup and failback verified
```

Passing an earlier stage does not imply that later stages pass.

## Safety gates

- The contract must specify whether an isolated network is required.
- Production failover is rejected unless the contract explicitly allows it.
- All named approval roles are required before a drill can be evaluated as authorized.
- Dependency cycles and references to unknown components fail validation.
- A missing validation is treated as failed, not passed.
- The current implementation replays evidence and performs no Azure mutations.

## Economics

The reference evaluator calculates drill infrastructure cost and an estimated revenue exposure based
on the workload's monthly revenue and observed recovery duration. This is a prioritization aid. A
real engagement must replace it with the customer's contribution margin, transaction patterns,
contractual penalties and operational costs before making investment decisions.

## Production adapters still required

- Recovery Services vault inventory
- Azure Backup recovery points and restore jobs
- Site Recovery protected items, recovery plans and test-failover jobs
- AKS persistent-volume protection through the selected technology
- DNS, TLS, identity and network reachability assertions
- Database consistency and application-specific synthetic transactions
- Cleanup and failback evidence
- Metered cost observations

These adapters must remain separated from recovery authorization. Evidence collection must not be
able to initiate a restore or failover.

