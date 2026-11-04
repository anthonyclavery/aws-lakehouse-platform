# Cost and destruction

## Measured usage and planning models

The final acceptance ran in eu-west-3 on 2026-11-04. Usage and the regional AWS Price List Query API records are retained in [reviewed evidence](evidence/final-acceptance-2026-11-04.json). Models exclude free-tier credits, negotiated discounts and taxes. Developer/local-machine/AI-assistant costs are outside these AWS figures.

| Item | Observation or assumption |
| --- | --- |
| Glue 5 standard ETL | USD 0.44 per DPU-hour; two G.1X workers, one DPU each; observed service execution seconds with a 60-second minimum per run |
| Athena Paris | USD 7 per TB; conservative decimal MB/TB model, upward MB rounding and 10 MB floor for successful DML queries |
| CodeBuild Linux small | USD 0.005 per minute; rounded total wall minutes used as an upper model, including queue/provisioning time |
| S3 Standard first tier | USD 0.024 per GB-month; ending observed inventory held for the full campaign duration, using a 730-hour month |
| Entire acceptance | USD 1.5847 planning model, including the failed cold-start cleanup, expected business rejection and empty cleanup |
| Explicit unmetered allowance | USD 0.5000 for requests/catalog/logs/transfer/billing APIs; not claimed complete event metering |
| Idle month | USD 0.6501 modeled with thirty daily empty cleanup builds, current versioned-state bytes and USD 0.50 miscellaneous allowance |
| Low-use month | USD 3.5853 modeled for four comparable compact demonstrations and their manual cleanups, in addition to the idle baseline |
| Operator stop thresholds | USD 5 per campaign and USD 20 per month; planning rules, not automatically enforced AWS Budgets/caps |

Separate compact/full-domain usage, native worker seconds, scan bytes, cleanup wall time, selected regional SKUs/effective dates, retained state versions and all arithmetic assumptions are in the evidence. Ending-inventory storage and wall-minute models may overstate actual charges; shared logs and other unmetered activity remain allowances. A current zero Cost Explorer amount does not mean free execution.

Project-tagged Cost Explorer UnblendedCost was queried with the active `Project=aws-lakehouse-platform` allocation tag. Its daily observations are retained with their estimated flags. They include earlier qualification activity, can lag, and do not provide a settled invoice for this session or attribution of untagged shared services. Recheck billing after settlement; no delayed amount is presented as a verified final charge.

The older partition/compaction reports explicitly modeled scan charges at USD 5/TB. Their raw scan measurements, fingerprints and historical records remain intact. The current Paris catalog rate is USD 7/TB. Under the same decimal/minimum assumptions, the partition example becomes USD 0.000126 versus 0.000070 per query and USD 0.000588 for six queries; compaction becomes USD 0.000070 in both phases and USD 0.000420 for six. These are revised models, not new benchmark executions or invoices.

Sources: [AWS Glue pricing](https://aws.amazon.com/glue/pricing/), [Athena pricing](https://aws.amazon.com/athena/pricing/), [CodeBuild pricing](https://aws.amazon.com/codebuild/pricing/), [S3 pricing](https://aws.amazon.com/s3/pricing/) and the region-specific Price List records in the evidence. Rates describe the qualified Glue 5/classic worker configuration, not Glue 6 or Flex prices.

## Resources retained while idle

The data-plane bucket, Glue database/jobs, execution inline policy, analyst workgroup, three temporary roles/policies and scoped LF registrations/grants are removed after demonstrations. There is no continuously running processing cluster.

Persistent deployment identities, encrypted/versioned Terraform state, cleanup CodeBuild/Scheduler roles/project and short-retention cleanup logs remain intentionally. The running cleanup cannot safely destroy itself. Default/shared Glue job log streams can also remain outside the Terraform data-plane scope; they are not claimed deleted or fully tag-attributed. Their storage is covered by the stated allowance and should be reviewed before repeated campaigns grow retention. Removing the control plane is a separate reviewed procedure in [automatic cleanup](automated-cleanup.md).

The enabled schedule invokes cleanup daily at 20:00 Europe/Paris, including after the data plane is already absent. Those empty builds are real work and are included in the idle model. IAM roles and the CodeBuild project have no continuously allocated worker, while versioned state and retained logs still have storage/request costs. These assumptions are specific to this small, short-lived demonstration.

## Controlled destruction

Use `run_demo.py cleanup` from the same workspace and clean pushed revision. It verifies the isolated state namespace/expiry and terminal job state, invokes native guarded deletion and preserves its receipt. The exact saved destroy plan is checked against named resources/tags; no account-wide tag scan or deletion is used. Protected deployment identities/state remain outside the deletion allowlist.

The guard's existing two-pass LF-revocation recovery accepts only verified progress and a strictly smaller remaining deletion subset. The final qualification independently checked scoped grants/registrations, IAM policies and restored LF account settings, then exercised an empty native cleanup. See [final acceptance](final-acceptance.md) for the actual results and limits.
