# Azure AI production engineering and LLMOps factory

This module evaluates model and agent release candidates using business value, quality,
groundedness, tool correctness, reliability, latency, loop behavior and cost. It complements
Microsoft Foundry, LangSmith, NVIDIA NIM, LiteLLM and OpenTelemetry rather than recreating them.

## Release loop

1. Normalize provider, model, retrieval, tool, cost and outcome evidence.
2. Detect repeated-tool loops and step-budget violations.
3. Calculate completion, provider reliability and cost per completed workflow.
4. Calculate net contribution value rather than selecting the cheapest inference.
5. Reject candidates that regress quality, groundedness, tool correctness or business value.
6. Select the highest-value eligible candidate for shadow evaluation only.
7. Require canary evidence and Change Assurance before any production promotion.
8. Feed production failures back into evaluation datasets and release contracts.

The synthetic scenario compares Microsoft Foundry routing, a small self-hosted model, NVIDIA NIM
and a frontier provider. The cheap model is rejected because lower completion and quality destroy
more value than inference savings create. The winner is eligible only for shadow traffic.

## Production adapters

- Microsoft Foundry and Azure OpenAI
- NVIDIA NIM, vLLM and Ollama
- Anthropic and OpenRouter-compatible APIs
- LiteLLM model gateway
- LangSmith, Langfuse, Phoenix and MLflow evaluation evidence
- OpenTelemetry traces and Azure Monitor
- Azure AI Search, GraphRAG, PostgreSQL/pgvector and vector databases
- FinOps Value Realization and Change Assurance modules

## KPI contract

- completion, groundedness, correct-tool and escalation rates;
- retrieval recall, precision, freshness and citation correctness;
- P50/P95/P99 latency, provider failures and failover success;
- loop rate, steps per successful workflow and token waste;
- cost per completion, resolution, lead and tenant;
- revenue per 1,000 interactions and net contribution value;
- evaluation coverage, regression escape rate and time to verified fix.

## Commercial boundary

Indicative ranges are USD 5,000–15,000 for readiness assessment, USD 15,000–50,000 for evaluation
and instrumentation, USD 25,000–100,000 for production deployment and USD 5,000–25,000 monthly for
managed AI SRE/LLMOps. These are positioning ranges, not quotes or guaranteed outcomes.
