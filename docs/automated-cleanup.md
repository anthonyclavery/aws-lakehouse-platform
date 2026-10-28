# Guarded automatic cleanup

## Purpose

The cleanup control plane is deliberately separate from the demonstration state. It contains a private state bucket, a CodeBuild project, a dedicated build role, a Scheduler execution role, and a short-retention CloudWatch log group. These resources are not data-plane demonstration resources and remain after a successful cleanup so that the result can be reviewed.

## Daily invocation

The schedule is disabled by default. For an approved demonstration, its operator supplies a `cleanup_demo_id` and the exact UTC expiry value. When enabled, it invokes the cleanup CodeBuild project every day at 20:00 in the `Europe/Paris` time zone, with no retry.

The build obtains the `control-plane/terraform.tfstate` key only. It generates a targeted destroy plan for the data-plane bucket, its access and encryption configurations, the Glue database, and the random bucket suffix. The local guard requires either an empty plan, or exactly those resource addresses with deletion actions only and the expected `Project`, `Environment`, `DemoId`, `AutoCleanup`, and `ExpiresAt` tags. It rejects every other plan before Terraform can apply it.

The dedicated deployment user, Terraform operator role, and their policies are persistent control-plane identities. They are never in the automatic cleanup scope and use `prevent_destroy`, so the next demonstration can be deployed with the same dedicated profile.

After a successful destruction, later daily runs produce an accepted no-op plan. If a build fails, the next daily run remains bounded by the same guard; it does not expand its scope.

## Activation checklist

The operator reviews the main Terraform plan and the bootstrap control-plane plan before applying either. The schedule is enabled only with the exact demonstration identifier and the matching expiry timestamp from the main deployment. The CodeBuild source is `main`, so the cleanup branch must be merged and its CI checks green before an AWS demonstration uses this mechanism.

The cleanup plan guard is intentionally brittle. Adding a new data-plane resource requires updating its allowlist and tests in `src/lakehouse_platform/cleanup_guard.py` before automatic destruction can proceed. This fails closed rather than risking an unreviewed deletion.

## Removal of the control plane

The control plane is not removed by the scheduled job. Its explicit removal is a separately reviewed Terraform action. The state bucket uses `prevent_destroy` and must never be deleted while it still stores a usable state history.
