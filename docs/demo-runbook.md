# Demonstration runbook

## Purpose

The demonstration proves the pipeline with controlled synthetic data. It is not a continuously running IoT platform and does not expose a public endpoint.

## Planned flow

| Step | Evidence |
| --- | --- |
| Provision a short-lived environment with Terraform | Reviewed Terraform plan and created resources |
| Generate and deposit the initial synthetic batches | Immutable S3 manifests with no personal data |
| Run reference-data processing | Iceberg history tables with valid temporal relationships |
| Run index processing | Validated indexes, consumption intervals, and quarantine results |
| Run gold aggregation | Athena results for customer and site consumption and cost |
| Deposit an incremental normal batch | Updated results without a full-history rebuild |
| Deposit a late or corrected batch | Bounded recalculation evidence and changed lineage |
| Verify analyst access | Athena access to gold only, with raw access denied |
| Run one benchmark | Measured report, not a projected claim |
| Destroy the environment | Terraform destruction and post-destruction verification |

## Evidence rules

Every capture, log extract, query result, and benchmark report is reviewed before publication. It must contain only synthetic identifiers and must not expose an AWS account identifier, local path, credential, raw configuration value, or personally identifying information.

The visual sequence is available in [the demonstration workflow diagram](diagrams/demo-workflow.md).
