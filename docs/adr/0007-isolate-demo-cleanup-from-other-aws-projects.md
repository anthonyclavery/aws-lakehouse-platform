# ADR 0007: Isolate demo cleanup from other AWS projects

## Status

Accepted.

## Context

The AWS account also hosts other active projects. An automated Lakehouse demonstration cleanup must never discover, modify, stop, or delete resources outside its own explicit scope.

## Decision

Every taggable Lakehouse demonstration resource uses the following tags: `Project = aws-lakehouse-platform`, `Environment = demo`, `ManagedBy = terraform`, `DemoId`, `AutoCleanup = true`, and `ExpiresAt`.

The cleanup process uses an isolated Terraform state key for one `DemoId`. It verifies the expected project, environment, and demo identifier before running `terraform destroy`. The cleanup role is restricted to Lakehouse resource names, prefixes, and ARNs. Resources that cannot be tagged use the same project-specific naming convention and remain present only in the scoped Terraform state.

## Consequences

Tags are an additional verification and a cost-allocation mechanism, not an account-wide resource-discovery mechanism. The cleanup process does not search for broadly tagged resources across the AWS account.

The automatic cleanup destroys the data-plane demonstration environment. A minimal control plane for cleanup, such as an isolated state store and cleanup build project, may remain intentionally outside the ephemeral demo scope. It must be documented, low cost at rest, and removable through a separate explicit procedure.

The mechanism must be proven with a disposable demonstration while other project resources remain present, followed by evidence that they were not modified.
