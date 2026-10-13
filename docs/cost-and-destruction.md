# Cost and destruction

## Cost discipline

AWS resources exist only for a demonstrable purpose. Glue jobs, benchmark workloads, and Athena scans are launched on demand. The V1 design has no continuously running stream processor, cluster, or public service.

Actual costs will be measured only after a real deployment. The repository will distinguish cost at rest, demonstration cost, low-use monthly cost, assumptions, and a safety ceiling. No estimate will be presented as a verified cost before execution.

## Destruction requirement

Terraform must provide a repeatable destruction path for all demo resources. After each AWS demonstration, the operator must remove billable resources that are no longer needed and verify the result.

When automatic cleanup is enabled, a one-time EventBridge Scheduler invocation at 22:00 `Europe/Paris` starts a project-scoped cleanup job. The cleanup uses the isolated Terraform state for one demonstration and verifies its `Project`, `Environment`, and `DemoId` before it runs. It must not perform an account-wide tag scan or operate on unrelated projects.

The data-plane demonstration resources are ephemeral. A minimal cleanup control plane may remain intentionally after the run because a running cleanup job cannot safely delete itself. Its purpose, cost, permissions, and separate removal procedure must be documented.

If a small S3 evidence set is retained, it must contain only reviewed synthetic artifacts. Its remaining storage cost and retention decision must be documented explicitly.
