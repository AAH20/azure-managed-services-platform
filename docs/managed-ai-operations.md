# Managed AI operations: first live slice

This release makes one OpenAI-compatible private inference endpoint operationally observable. An operator defines a customer service, runs an active synthetic inference probe, stores its timestamped result in a local SQLite database and renders a monthly report. The example service points to loopback and is fictional. No customer endpoint, GPU cluster or paid subscription has been connected here.

## Operator runbook

Use a customer-approved service definition in a private directory. It contains service ID, customer, endpoint URL, model, named operator and sample targets; it must contain **no API key**. HTTPS is required except for loopback testing. Set `AZURE_MSP_ENDPOINT_TOKEN` only in the process environment if the endpoint requires a bearer token. Keep the SQLite database in private, backed-up storage because it contains customer identifiers and service observations.

```bash
azure-msp-managed-ai probe /private/path/service.json --db /private/path/observations.sqlite
azure-msp-managed-ai report /private/path/service.json \
  --db /private/path/observations.sqlite --month 2026-09 \
  --json-output /private/path/september.json \
  --html-output /private/path/september.html
```

Run `probe` from a trusted scheduler at a declared cadence, such as every five minutes; configure that scheduler, alerting, backup and on-call ownership in the customer's environment. A nonzero probe exit code indicates a failed sample and can trigger an external alert. The command makes only `GET /health` and one streaming `POST /v1/chat/completions` request. It never mutates cluster resources. It stores only success, latency, time to first output token and a coarse error type; it does not retain prompt or model response content. The probe's fixed prompt is `Reply with the single word: ready`.

The monthly report counts samples, computes success percentage and p95 probe latency/TTFT, and compares those figures with the service definition. No data yields `unknown`; fewer than `minimum_samples_per_month` yields `insufficient_data`, even if every recorded sample passed. Set that minimum from the agreed probe cadence and month length. The HTML is a static local artifact; this release does not implement an authenticated multi-tenant customer portal.

## What the numbers mean

Sample success is **not continuous availability**. Five-minute probes can miss short outages. The probe tests one tiny synthetic request and cannot prove customer workload quality, sustained throughput or contractual SLO performance. It records neither GPU utilization nor billed spend. For a paid 30-day pilot, pair this report with production telemetry, incident tickets, billing records, a rollback drill, and customer-approved quality tests. Report missed samples, scheduler outages and maintenance windows explicitly; do not infer them from the database.

The first commercial milestone remains one customer-approved endpoint operated for 30 days with a named on-call owner, a real deployment, validated telemetry and a signed service agreement. This OSS command is an operator aid, not that completed service.
