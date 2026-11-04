# AWS Lakehouse Platform
A production-minded AWS Lakehouse reference implementation for synthetic smart-meter data.

The project turns cumulative electricity-meter indexes into auditable consumption and energy-cost data. It demonstrates reliable incremental processing with Amazon S3, Apache Iceberg, AWS Glue, PySpark, Amazon Athena, AWS Lake Formation, and Terraform.

## Why this project exists

The interesting part of a Lakehouse is not the list of AWS services. It is the ability to process cumulative meter indexes rather than ready-to-use consumption values; duplicate deliveries, late arrivals, source corrections, and targeted backfills; meter rollovers and replacements; temporal customer, site, meter, contract, and tariff relationships; schema versions, data contracts, data quality, lineage, and access control; and partition pruning alongside the small-file problem.

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

The [implemented analyst boundary](docs/analyst-access.md) combines Lake Formation grants on the three Gold products with IAM refusal of Silver/internal metadata and direct Bronze/warehouse objects. A separate [ingestion role](docs/ingestion-access.md) permits only conditional Bronze publication and replay reads, with transformation and project-operation identities kept separate. Terraform provisions and later destroys the cloud resources required for a short, controlled demonstration.

Further design details are in [docs/architecture.md](docs/architecture.md).

## Data products

The primary gold products are `daily_site_energy` and `daily_customer_energy`. They expose energy consumption and synthetic energy cost for a chosen day, month, year, or custom time range. `monthly_customer_energy` supports common monthly analysis.

The project intentionally models energy cost only. It does not calculate a complete utility bill, fixed subscription charges, taxes, network charges, or VAT.

## Validation and demonstration

The local-first workflow validates contracts, business rules, and deterministic scenarios before AWS resources are used. The AWS demonstration then loads synthetic batches, runs incremental Glue jobs, queries results with Athena, proves restricted analyst access, and injects selected late or corrected events.

Bronze ingestion uses a versioned Python script. It verifies the generated manifest and checksums, creates each object with an S3 conditional write, and publishes the manifest last. Replays verify stored bytes; concurrent writers cannot replace an existing object through this uploader. See [Bronze landing ingestion](docs/landing-ingestion.md).

The partition benchmark compares identical unpartitioned and day-partitioned Iceberg datasets through repeated Athena queries. The compaction benchmark measures the same fragmented table before and after bin-pack maintenance, with full-content verification and durable recovery. Results are published only after real execution and verification.

See [docs/demo-runbook.md](docs/demo-runbook.md) and [docs/benchmark-methodology.md](docs/benchmark-methodology.md).

## Local quick start

The local foundation has no third-party runtime dependency and does not access AWS.

For a guided business demonstration, follow the [single-entry-point runbook](docs/demo-runbook.md). `scripts/run_demo.py` provides an offline five-phase preview, explicit AWS plan/deploy/run/query/cleanup stages, three named analyst queries and readable reports. See the [generated local report](docs/examples/demo-local-report.md). The [final AWS acceptance](docs/final-acceptance.md) includes the combined CLI, complete historical 200-site domain, controlled invalid inputs and cleanup. See the [actual AWS report](docs/examples/demo-aws-report.md).

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable .
python -m unittest discover -s tests -v
python scripts/generate_initial_data.py --customers 4 --sites 8 --output-dir artifacts/example
```

Generated batches use newline-delimited JSON. They are intentionally ignored by Git and can be regenerated from the seed recorded in their manifest. The default command generates the documented 100-customer, 200-site scenario. Use `--without-indexes` to inspect only the reference flows without generating historical indexes.

`scripts/run_local_silver.py` is a local business-rule oracle. The [provisioned Glue Silver job](jobs/silver_process_indexes.py) writes Iceberg indexes and bounded consumption intervals with durable recovery.

## Project status

V1 is accepted for an operator-led, on-demand AWS demonstration. The [final qualification](docs/final-acceptance.md) proves the complete CLI, analyst queries/report, the 100-customer/200-site historical domain, exact quarantine contents and replay, atomic rejection of a contradictory correction, access isolation and guarded cleanup. The [cost report](docs/cost-and-destruction.md) distinguishes observed usage, modeled charges and delayed tagged billing. Qualification environments were destroyed; persistent state/cleanup controls remain. Production SLAs, tenant isolation, concurrent writers and automatic schema migration are outside this V1. Existing partition/compaction measurements retain their published scope and limitations.

## Documentation

| Document | Purpose |
| --- | --- |
| [Architecture](docs/architecture.md) | Bronze, silver, gold layers and AWS responsibilities |
| [Data contracts](docs/data-contracts.md) | Input flows, keys, units, versions, and constraints |
| [Bronze landing ingestion](docs/landing-ingestion.md) | Conditional source writes, byte-verified replay and native concurrency qualification |
| [Business rules](docs/business-rules.md) | Consumption, tariff, rollover, replacement, and backfill rules |
| [Gold pricing jobs](docs/glue-gold-jobs.md) | Shared tariff calculation, local rebuild, Glue execution and validation limits |
| [Bounded Gold refresh](docs/bounded-gold.md) | Silver ledger consumption, old/new group footprints and durable recovery |
| [Silver reference job](docs/glue-reference-job.md) | Provisioned loader and sequential execution of the four reference flows |
| [AWS qualification](docs/aws-qualification.md) | Athena reconciliation, operational permissions and evidence procedure |
| [Integrated AWS identity chain](docs/integrated-chain.md) | Source-shaped ingestion, real Silver/Gold runs and analyst verification before/after governed updates |
| [Governed late/correction/replay campaign](docs/late-correction-replay.md) | Late indexes, higher versions and genuine fresh-Gold processing replays with all dedicated roles |
| [Bounded Silver recalculation](docs/bounded-silver.md) | Neighbour intervals, durable retry work and unchanged replay |
| [Final V1 acceptance](docs/final-acceptance.md) | Native combined CLI, historical 200-site domain, controlled invalid inputs, costs and teardown |
| [Demo runbook](docs/demo-runbook.md) | Unified local/AWS commands, analyst queries, readable reports and cleanup |
| [Benchmark methodology](docs/benchmark-methodology.md) | Reproducible partition pruning and compaction measurements |
| [Partition benchmark](docs/partition-benchmark.md) | Deterministic Iceberg pair, repeated Athena measurements and evidence |
| [Compaction benchmark](docs/compaction-benchmark.md) | Same-table before/after measurements, full-content proof and rewrite recovery |
| [Security and privacy](docs/security-and-privacy.md) | Public-repository and access-control guardrails |
| [Analyst access](docs/analyst-access.md) | Scoped LF Gold grants, real read/denial tests and protected teardown |
| [Ingestion access](docs/ingestion-access.md) | Temporary Bronze-only role, enforced conditional writes and access qualification |
| [Cost and destruction](docs/cost-and-destruction.md) | Cost discipline and post-demo cleanup requirements |
| [Architecture decision records](docs/adr/README.md) | Accepted architectural decisions and their consequences |
| [Architecture diagrams](docs/diagrams/v1-architecture.md) | Platform flow and access boundaries |
| [Data relationships](docs/diagrams/data-relationships.md) | Temporal entity relationships and consumption derivation |
| [Demo workflow](docs/diagrams/demo-workflow.md) | Local validation, controlled AWS demonstration, and cleanup flow |

## Public-repository guardrails

No AWS account identifiers, personal data, real customer data, real tariffs, credentials, Terraform state, local paths, or raw unreviewed logs belong in this repository. Screenshots and exported evidence must be reviewed before publication.
