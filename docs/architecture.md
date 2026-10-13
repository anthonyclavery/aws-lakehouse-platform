# Architecture

## Design goal

The platform converts cumulative smart-meter indexes into auditable consumption and synthetic energy-cost products. It is designed for deterministic local validation first and a short, reproducible AWS demonstration second.

## Layers

### Bronze

Bronze is the immutable landing layer in Amazon S3. It retains source-shaped synthetic files and their manifests. A batch is never edited after it is deposited, which makes it possible to audit or replay processing.

### Silver

Silver is the operational Iceberg layer. AWS Glue and PySpark validate the input contracts, apply source versions, preserve temporal history, calculate consumption intervals, and send invalid records to quarantine.

The planned silver tables are `site_meter_history`, `customer_site_history`, `site_contract_history`, `tariff_grid_history`, `consumption_index`, `meter_consumption_interval`, `quarantine_records`, and `ingestion_batch_audit`.

### Gold

Gold contains analyst-facing Iceberg products: `daily_site_energy`, `daily_customer_energy`, and `monthly_customer_energy`. Athena queries these tables without requiring analyst access to raw objects or operational silver tables.

## AWS responsibilities

| Component | Responsibility |
| --- | --- |
| Amazon S3 | Immutable landing batches, Iceberg warehouse data, Athena results, benchmark artifacts |
| AWS Glue Data Catalog | Iceberg table catalog |
| AWS Glue and PySpark | Validation, merges, interval calculation, tariff allocation, aggregation, maintenance |
| Apache Iceberg | Atomic table updates, snapshots, schema evolution, `MERGE INTO`, and compaction |
| Amazon Athena | Analyst queries and reproducible benchmark queries |
| AWS Lake Formation | Least-privilege table access for ingestion, transformation, and analyst roles |
| AWS IAM and encryption | Execution identity, controlled access, and encryption configuration |
| Terraform | Reproducible creation and destruction of the demonstration environment |
| Amazon CloudWatch | Glue execution logs and observable batch metrics |

## Processing sequence

Reference flows are applied before consumption indexes. The pipeline first resolves the temporal site-to-meter, customer-to-site, site-to-contract, and tariff relationships. It then validates indexes, identifies the previous index for the same meter, calculates consumption intervals, and applies tariffs.

Corrected or late data triggers a bounded recalculation. A corrected index can affect the interval immediately before and after it. A corrected tariff can affect only intervals covered by that tariff rule and effective period.

## Access model

The ingestion role writes only landing batches. The transformation role reads raw data and writes Iceberg tables. The analyst role reads approved gold tables through Athena. Lake Formation is the control point for the data-plane separation.

No persistent streaming service or publicly exposed endpoint is required for V1.

Rendered diagrams are available in [V1 Architecture](diagrams/v1-architecture.md), [Data relationships](diagrams/data-relationships.md), and [Demonstration workflow](diagrams/demo-workflow.md).
