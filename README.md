# AWS Lakehouse Platform
A production-minded AWS Lakehouse reference implementation for synthetic smart-meter data.

The project turns cumulative electricity-meter indexes into auditable consumption and energy-cost data. It demonstrates reliable incremental processing with Amazon S3, Apache Iceberg, AWS Glue, PySpark, Amazon Athena, AWS Lake Formation, and Terraform.

## Why this project exists

The interesting part of a Lakehouse is not the list of AWS services. It is the ability to process cumulative meter indexes rather than ready-to-use consumption values; duplicate deliveries, late arrivals, source corrections, and targeted backfills; meter rollovers and replacements; temporal customer, site, meter, contract, and tariff relationships; schema evolution, data contracts, data quality, lineage, and access control; and partition pruning alongside the small-file problem.

All data is deterministic, synthetic, and non-personal. The project does not reproduce a real utility, tariff, customer, architecture, or dataset.

## Domain scenario

The V1 dataset models 100 synthetic customers and 200 electricity-consumption sites. Eighty customers have one site and twenty customers have multiple sites. Each site has one active meter at a time; replacements preserve the relationship history.

The active contract distribution is deliberately fixed: 100 `BASE`, 80 `WEEKEND`, and 20 `PEAK_OFF_PEAK` contracts. `BASE` sites provide one index per day, while the other plans provide one index per hour. The oldest contracts have up to three years of history and four recent contracts have only a few days of history.

Meter indexes and calculated consumption use integer watt-hours (`Wh`) internally. Athena-facing products expose `kWh` and synthetic energy cost in euros.

## Architecture

```text
Synthetic generator
        |
        v
S3 landing zone: immutable batches and manifests
        |
        v
Glue and PySpark: reference data, indexes, validation, interval calculation
        |
        v
Apache Iceberg silver tables: validated and traceable operational data
        |
        v
Glue and PySpark: tariff allocation and aggregates
        |
        v
Apache Iceberg gold tables queried through Athena
```

Lake Formation separates ingestion, transformation, and analyst access. Terraform provisions and later destroys the cloud resources required for a short, controlled demonstration.

Further design details are in [docs/architecture.md](docs/architecture.md).

## Data products

The primary gold products are `daily_site_energy` and `daily_customer_energy`. They expose energy consumption and synthetic energy cost for a chosen day, month, year, or custom time range. `monthly_customer_energy` supports common monthly analysis.

The project intentionally models energy cost only. It does not calculate a complete utility bill, fixed subscription charges, taxes, network charges, or VAT.

## Validation and demonstration

The local-first workflow validates contracts, business rules, and deterministic scenarios before AWS resources are used. The AWS demonstration then loads synthetic batches, runs incremental Glue jobs, queries results with Athena, proves restricted analyst access, and injects selected late or corrected events.

The two planned benchmarks measure partition pruning and the effect of Iceberg compaction on fragmented files. Results will be added only after real execution and verification.

See [docs/demo-runbook.md](docs/demo-runbook.md) and [docs/benchmark-methodology.md](docs/benchmark-methodology.md).

## Project status

Design phase. The business model and public documentation are being reviewed before implementation starts. No AWS deployment, benchmark result, or cost claim has been made yet.

## Documentation

| Document | Purpose |
| --- | --- |
| [Architecture](docs/architecture.md) | Bronze, silver, gold layers and AWS responsibilities |
| [Data contracts](docs/data-contracts.md) | Input flows, keys, units, versions, and constraints |
| [Business rules](docs/business-rules.md) | Consumption, tariff, rollover, replacement, and backfill rules |
| [Demo runbook](docs/demo-runbook.md) | Planned controlled AWS demonstration |
| [Benchmark methodology](docs/benchmark-methodology.md) | Reproducible partition pruning and compaction measurements |
| [Security and privacy](docs/security-and-privacy.md) | Public-repository and access-control guardrails |
| [Cost and destruction](docs/cost-and-destruction.md) | Cost discipline and post-demo cleanup requirements |
| [Architecture decision records](docs/adr/README.md) | Accepted architectural decisions and their consequences |
| [Architecture diagrams](docs/diagrams/v1-architecture.md) | Platform flow and access boundaries |
| [Data relationships](docs/diagrams/data-relationships.md) | Temporal entity relationships and consumption derivation |
| [Demo workflow](docs/diagrams/demo-workflow.md) | Local validation, controlled AWS demonstration, and cleanup flow |

## Public-repository guardrails

No AWS account identifiers, personal data, real customer data, real tariffs, credentials, Terraform state, local paths, or raw unreviewed logs belong in this repository. Screenshots and exported evidence must be reviewed before publication.
