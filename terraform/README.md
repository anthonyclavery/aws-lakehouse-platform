# Terraform foundation

This directory contains the demonstration infrastructure. Its Terraform state is stored in a dedicated private S3 bucket, with encryption, versioning, native S3 lock files, and `prevent_destroy` protection. The bucket is separate from lakehouse data and is not subject to demonstration cleanup. The state bucket itself is managed by the isolated `bootstrap-state` configuration.

The account-specific backend settings are intentionally kept in the ignored `backend.hcl` file. They are created during the local bootstrap and must never be committed.

It creates an encrypted private S3 bucket and a Glue catalog database for one explicit demo identifier. Every taggable resource receives the project isolation tags defined in the repository security documentation.

Glue resources include the Silver index job, the [reusable reference-data job](../docs/glue-reference-job.md), and both [Gold jobs](../docs/glue-gold-jobs.md), with their S3 scripts. `silver_reference_job_name`, `gold_job_names` and `glue_database_name` expose the names needed for explicit launches. Creating resources does not start processing.

Copy `terraform.tfvars.example` to a local ignored `terraform.tfvars` only when an explicit AWS demonstration is approved. The example values are placeholders and are not a deployment instruction.

The optional [ingestion identity](../docs/ingestion-access.md) uses `ingestion_access_enabled` and a separate same-account `ingestion_governance_profile`; it adds only a tagged role and inline policy. `ingestion_access` exposes its role ARN. Update the reviewed operator-assumption and bootstrap cleanup prerequisites before enabling it. Keep the enabled flag in subsequent plans; it defaults to false.

The `bootstrap-state` configuration also defines the disabled-by-default cleanup control plane. It creates a daily 20:00 `Europe/Paris` schedule only when explicit cleanup inputs are supplied. Its CodeBuild job verifies the destroy plan before applying it. See [the guarded cleanup design](../docs/automated-cleanup.md).
