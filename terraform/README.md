# Terraform foundation

This directory contains the demonstration infrastructure. Its Terraform state is stored in a dedicated private S3 bucket, with encryption, versioning, native S3 lock files, and `prevent_destroy` protection. The bucket is separate from lakehouse data and is not subject to demonstration cleanup. The state bucket itself is managed by the isolated `bootstrap-state` configuration.

The account-specific backend settings are intentionally kept in the ignored `backend.hcl` file. They are created during the local bootstrap and must never be committed.

It creates an encrypted private S3 bucket and a Glue catalog database for one explicit demo identifier. Every taggable resource receives the project isolation tags defined in the repository security documentation.

Copy `terraform.tfvars.example` to a local ignored `terraform.tfvars` only when an explicit AWS demonstration is approved. The example values are placeholders and are not a deployment instruction.
