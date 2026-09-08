# Azure capacity and performance engineering factory

This module evaluates demand forecasts against every required workload dependency, quota,
provisioning lead time and load-test result. It complements Azure Autoscale, AKS Automatic, KEDA,
HPA/VPA and infrastructure optimizers rather than replacing their scaling controllers.

## Capacity loop

1. Validate forecast freshness, confidence, peak and upper bound.
2. Model normalized capacity across compute, data, network, AI and recovery dependencies.
3. Reject portfolios that omit a dependency, lack quota, arrive late or fail load testing.
4. Compare complete portfolios using protected revenue, capacity cost and abandonment exposure.
5. Advance the best eligible portfolio to an isolated load test only.
6. Use Change Assurance for production and FinOps for post-event value verification.

The synthetic product-launch scenario shows that scaling AKS alone fails because SQL, NAT, NIM,
DR capacity and vCPU quota remain insufficient. A full-chain pre-scale portfolio is selected, but
production scaling remains unauthorized.

## Production adapters

- Azure Monitor Autoscale, VM Scale Sets, AKS Automatic, HPA/VPA and KEDA
- Azure SQL/PostgreSQL, Cosmos DB, Redis, storage, Event Hubs and Service Bus
- Azure Firewall, NAT Gateway, Application Gateway, ExpressRoute and network assurance
- NVIDIA NIM, Azure AI endpoints, GPU quotas and alternate-provider routing
- Azure Load Testing, k6, Locust, OpenTelemetry and business transactions
- Fabric forecasts, Terraform/OpenTofu/Bicep plans and Cost Management

## KPI and commercial boundary

Track forecast error, time to ready capacity, dependency bottlenecks, quota utilization, GPU
utilization, throughput, P95/P99 latency, queue age, DR coverage, idle cost and protected margin.
Indicative ranges are USD 7,500–25,000 for assessment, USD 20,000–75,000 for predictive capacity,
USD 25,000–150,000 for AI/GPU engineering and USD 5,000–25,000 monthly for managed capacity SRE.
These are positioning ranges, not quotes, customer evidence or guaranteed outcomes.
