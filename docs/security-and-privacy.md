# Security and privacy

## Synthetic data only

All UUIDs, meter indexes, prices, contracts, sites, and customer relationships are generated synthetically. The repository must not contain real customer data, employee data, utility data, tariffs, or architecture details from any organization.

## Access control

The planned Lake Formation model separates three roles. Ingestion writes landing data only. Transformation reads landing data and writes Iceberg tables. Analysts read approved gold tables through Athena only.

## Demonstration isolation

Every taggable demonstration resource uses `Project = aws-lakehouse-platform`, `Environment = demo`, `ManagedBy = terraform`, a unique `DemoId`, `AutoCleanup = true`, and an `ExpiresAt` timestamp. The Terraform state bucket is the deliberate exception: it is private, encrypted, versioned, tagged with `Purpose = TerraformState`, and excluded from automatic cleanup so that the deployment can remain manageable. Its isolated bootstrap configuration uses a separate remote-state key.

The automatic cleanup process uses that state as its scope. It verifies the tags and demonstration identifier before destruction and has IAM permissions limited to Lakehouse resource names, prefixes, and ARNs. It must not discover or act on unrelated resources in the AWS account.

## Repository safety

Credentials, static access keys, account identifiers, Terraform state, populated variable files, private bucket names, unreviewed logs, and local paths are excluded from version control. Terraform examples use placeholders only. CI will include secret detection before public publication.

## Encryption and publication review

The deployed design will use AWS-managed encryption controls appropriate to each service. Before any commit containing evidence, screenshots and exported results are manually reviewed for identifiers or sensitive context.
