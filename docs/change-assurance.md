# Azure Infrastructure Change Assurance

Change Assurance evaluates infrastructure rollout evidence against an approved contract. It does
not execute deployments. A separate delivery system may consume the decision after independently
verifying identity, authorization and current state.

## Decision sequence

```text
IaC plan or Azure What-If
  -> normalized change contract
  -> monitoring and recovery prerequisites
  -> human approvals
  -> test observation
  -> canary observation
  -> production observation
  -> billing verification
```

Every ring must satisfy latency, error-rate and transaction-success thresholds. A missing
observation fails closed. A regression halts promotion and returns the declared rollback steps.

## Safety properties

- Production cannot be the first exposure ring.
- Production contracts must contain a production ring.
- Monitoring and recovery coverage must be verified.
- Every required approval must be present.
- Rollback steps are mandatory.
- Delete operations require a separate destructive-change contract.
- Missing health evidence halts rollout.
- Expected savings are not reported as realized savings without post-billing evidence.

## Current evidence boundary

The example is synthetic. It evaluates supplied observations and performs no Azure, Terraform,
OpenTofu, Bicep or Kubernetes operation. Production adapters must independently acquire and sign
plan, What-If, deployment and telemetry evidence.

