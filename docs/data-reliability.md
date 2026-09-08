# Azure data reliability and decision intelligence factory

This module evaluates data products against executable freshness, completeness, reconciliation,
schema, units and range contracts, then traverses lineage to identify affected dashboards, models
and agents. It complements Microsoft Fabric, Purview, Databricks, dbt and OpenLineage rather than
recreating their storage, transformation or catalog capabilities.

## Reliability loop

1. Normalize data-product contracts and runtime observations.
2. Distinguish technical pipeline success from semantic correctness.
3. Traverse downstream lineage and calculate business exposure.
4. Block publication and automated decisions when a contract fails.
5. Propose quarantine, last-verified-version and bounded backfill actions.
6. Validate recovered data before controlled publication through Change Assurance.

The synthetic Salesforce scenario changes annual contract value from decimal USD to integer cents.
The pipeline reports success, but type, unit, range and reconciliation controls fail. Power BI, a
demand forecast, an expansion agent and an inventory recommendation are identified downstream.

## Production adapters

- Microsoft Fabric Data Factory, OneLake, Power BI and Real-Time Intelligence
- Microsoft Purview, Azure Databricks, Snowflake and dbt
- OpenLineage/Marquez, Airflow and Dagster
- Great Expectations, Soda and warehouse-native tests
- Salesforce, SAP, Oracle, PostgreSQL and streaming sources
- MLflow/model features, RAG indexes and AI-agent consumers

## KPI and commercial boundary

Track contract pass rate, freshness, reconciliation, unknown lineage, bad-data detection and
recovery time, forecast error, decisions made on stale data and cost per trusted data product.
Indicative ranges are USD 7,500–25,000 for assessment, USD 20,000–75,000 for contracts and lineage,
USD 30,000–150,000 for platform reliability and USD 5,000–25,000 monthly for managed DataOps.
These are positioning ranges, not quotes, customer evidence or guaranteed outcomes.
