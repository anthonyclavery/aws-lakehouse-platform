# Native cleanup recovery qualification

The controlled cleanup handles one specific partial-effect failure: Lake Formation can remove the cleanup caller's own grant and still return a revoke error while resolving the now-invisible resource. A failed Terraform apply is never counted as success. The driver refreshes state, validates the complete saved destroy plan again, requires strictly fewer deletions and no remaining LF permission revocations, then permits one final apply. Any other error or second failure stops the job.

## Reproduce on a disposable demonstration

Use the existing controlled demo and exact cleanup-policy prerequisites from the [analyst access runbook](analyst-access.md). Keep persistent deployment identities and the versioned state bucket. Review the private provisioning plan before applying it. Do not change the cleanup job's global Lake Formation settings permissions; it needs only scoped grant delegation, read/revoke and deregistration access.

Create the canonical Gold fixture with the production Gold module, enable analyst governance using `scripts/enable_analyst_access.py`, and run `scripts/qualify_analyst_access.py`. Save original LF account settings, fresh Terraform outputs and the pre-cleanup state privately. Verify that all expected cleanup-address instances exist before starting the test. Original account settings must be restored before cleanup begins.

Commit and push the tested driver revision. Start the existing cleanup CodeBuild project with that exact commit as its `sourceVersion`. The deployment/qualification identity only starts and observes the build; the CodeBuild service role performs the entire cleanup. Do not intervene between its two apply attempts or temporarily grant it global administration. Leave the normal cleanup schedule enabled.

Read the terminal build result and its complete CloudWatch log. The final `GUARDED_DESTROY` JSON receipt records `passes`, `initial_delete_count`, and an optional `recovery` object. A real recovery proof requires a successful build, `passes=2`, only LF revoke errors in the first apply, the intervening refresh/revalidation message, a strictly smaller remaining deletion count and zero remaining LF revocations. A one-pass success proves ordinary cleanup only. Unit-test subprocess failures do not prove AWS recovery.

The driver also writes `destroy-initial-plan.json` and, on recovery, `destroy-recovery-plan.json` beside its saved plan in the build's temporary directory. These contain private infrastructure pointers. They are transient build files, not public evidence or a durable artifact export. Preserve reviewed raw logs and any explicitly exported plans privately; publish only sanitized counts, source revisions, checks and code fingerprints.

After completion, independently confirm that the bucket, Glue database/jobs, both workgroups, analyst/data IAM roles, Gold registrations and scoped grants are absent. Confirm that the data plane is absent from Terraform state, persistent identities and versioned state remain, original LF account settings match exactly, and the cleanup schedule remains enabled. An administrative read audit, if required to inspect removed grants, runs only after the build terminates and restores its settings in `finally`.

Run the same pushed revision again against the empty environment. Require a successful one-pass receipt with zero initial deletions and no recovery. This final run checks that subsequent scheduled cleanup remains harmless.

## Managed result on 2026-11-03

A fresh Glue fixture and all 35 analyst governance resources were deployed. Actual analyst Gold queries and Bronze/Silver denials passed before teardown. A private state audit confirmed all 57 cleanup instances existed. Account-wide LF settings were restored before starting the existing CodeBuild cleanup project at the pushed revision.

The single build succeeded after two apply attempts. Its first apply returned seven native LF revoke errors, with no other Terraform errors. The automatic refresh/revalidation reduced the deletion plan from 57 to eight resources, with zero remaining LF permission revocations. The driver then applied that saved subset successfully. There was no injected failure, manual inter-pass repair or temporary global LF administrator for the cleanup role.

Independent API/state checks confirmed deletion of demo data, jobs, database, workgroups, analyst/data roles, registrations and scoped grants. Persistent deployment identities, versioned Terraform state, cleanup context and the enabled schedule were preserved. Original LF settings matched exactly after the final administrative read audit.

A second CodeBuild invocation at the same source revision succeeded on the empty environment in one pass, with zero deletions, no errors and no recovery. The independent final checks passed again.

The [sanitized evidence](evidence/cleanup-recovery-2026-11-03.json) records source revision, native receipts, private-code fingerprints and final checks. The prior analyst qualification's limitation on native automatic recovery is superseded by this full-cycle result. Local validation passed 163 Python tests, six Terraform mock scenarios, formatting and both Terraform root validations.
