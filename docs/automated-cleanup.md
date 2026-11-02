# Guarded automatic cleanup

## Purpose

The cleanup control plane is deliberately separate from the demonstration state. It contains a private state bucket, a CodeBuild project, a dedicated build role, a Scheduler execution role, and a short-retention CloudWatch log group. These resources are not data-plane demonstration resources and remain after a successful cleanup so that the result can be reviewed.

## Daily invocation

The schedule is disabled by default. For an approved demonstration, its operator supplies a `cleanup_demo_id` and the exact UTC expiry value. When enabled, it invokes the cleanup CodeBuild project every day at 20:00 in the `Europe/Paris` time zone, with no retry.

## Manual invocation

The build derives its project name, demonstration identifier, expiry timestamp, and isolated state bucket from the account, region, and persistent `terraform_data.cleanup_context` record in the isolated demonstration state. A manual build therefore uses the same guarded scope as the schedule without requiring console overrides. The context record survives data-plane cleanup so later daily runs can safely validate an empty plan.

The build obtains the `control-plane/terraform.tfstate` key only. It generates a targeted destroy plan for the data-plane bucket, its access and encryption configurations, the Glue database and four jobs, uploaded scripts, the Athena workgroup, and the random bucket suffix. The Glue execution inline policy is also removed because it references the disappearing bucket/database. The execution role itself remains.

The local guard requires either an empty plan, or a subset of only approved addresses with deletion actions. Taggable resources require the exact `Project`, `Environment`, `DemoId`, `AutoCleanup`, and `ExpiresAt` tags. The untaggable execution inline policy instead requires both role and policy names to equal `aws-lakehouse-platform-demo-glue-silver`. No other IAM policy is accepted. Accepting a subset preserves idempotence if an earlier run removed some approved resources before failing. Every other plan is rejected before Terraform can apply it.

The dedicated deployment user, Terraform operator role, and their policies are persistent control-plane identities. They are never in the automatic cleanup scope and use `prevent_destroy`, so the next demonstration can be deployed with the same dedicated profile.

The cleanup role can remove current and versioned S3 objects, abort incomplete multipart uploads, and reset the bucket-level public-access and encryption configurations required by the Terraform provider before deleting the bucket. It can delete the project Glue jobs/database, project Athena workgroup and the exact Glue execution inline policy. IAM permissions for this policy are restricted to the Glue execution role; deployment identities remain outside the guard's deletion scope.

After a successful destruction, later daily runs produce an accepted no-op plan. If a build fails, the next daily run remains bounded by the same guard; it does not expand its scope.

Terraform JSON format 1.2 omits `resource_changes` for some no-op plans. The guard accepts this encoding only with explicit `applyable=false`, `errored=false` and a boolean `complete` field. A targeted plan may legitimately have `complete=false`. An absent change list without these markers, or an explicit null list, remains rejected. The live [qualification report](aws-qualification.md) records a full guarded destruction followed by a successful empty CodeBuild run.

## Activation checklist

The operator reviews the main Terraform plan and the bootstrap control-plane plan before applying either. The schedule is enabled only with the exact demonstration identifier and the matching expiry timestamp from the main deployment. The CodeBuild source is `main`, so the cleanup branch must be merged and its CI checks green before an AWS demonstration uses this mechanism.

The cleanup plan guard is intentionally brittle. Adding a new data-plane resource requires updating its allowlist and tests in `src/lakehouse_platform/cleanup_guard.py` before automatic destruction can proceed. This fails closed rather than risking an unreviewed deletion.

## Removal of the control plane

The control plane is not removed by the scheduled job. Its explicit removal is a separately reviewed Terraform action. The state bucket uses `prevent_destroy` and must never be deleted while it still stores a usable state history.
