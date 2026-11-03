# Analyst Gold access with Lake Formation

The analyst reads only `daily_site_energy`, `daily_customer_energy` and `monthly_customer_energy` through an isolated Athena workgroup. Standard Lake Formation registration and named `SELECT`/`DESCRIBE` grants govern those three existing Gold locations. No catalog-wide/table-wildcard grant, LF hybrid opt-in, service-linked data-access role or account-wide default-permission change is introduced.

Silver and internal tables retain the existing IAM-based operational model. The analyst IAM policy excludes them from Glue catalog access. Direct S3 reads/writes/deletes under the project `landing/` and `warehouse/` prefixes are explicitly denied, including direct Gold reads. Gold Athena queries instead use LF-vended credentials from a dedicated, read-only role restricted to the three exact Gold locations. This is a combined LF/IAM boundary, not a claim that Silver has been migrated wholesale into Lake Formation.

## Identities and storage

The ephemeral `<demo>-analyst` role trusts only the dedicated project Terraform operator role and has no static access key. Qualification assumes a 15-minute session. Its maximum session duration is one hour. It can query only the `<demo>-analyst` workgroup, whose engine 3, SSE-S3 result encryption, output location and 100 MiB scan cap are enforced. Result reads/writes/listing are restricted to `athena-results/analyst/`. The analyst has no LF grant-management, role-management, warehouse-write or alternate-workgroup permission.

`lakeformation:GetDataAccess` requires IAM `Resource="*"`; effective table authorization is the three named LF grants. The data-vending role trusts Lake Formation for the same account and can only list/read those Gold S3 prefixes. It cannot write/delete data, and the analyst cannot assume it directly. The two IAM roles and analyst workgroup carry all required demo cleanup tags.

The project operator retains explicit read-only Gold LF grants for reconciliation. The transformation role retains its table-creator permissions and registered-location access so Gold processing can continue. Governance and cleanup receive grantable rights only on the exact project Gold tables, their locations and database metadata. These control-plane rights do not belong to the analyst. The explicit writer permission set includes the expanded creator grants returned by LF, avoiding destructive refresh churn.

## Enable in two stages

First deploy the ordinary demonstration with `analyst_access_enabled=false`, load references/Silver and create the canonical Gold products using the existing transformation role. Keep writers paused while enabling/qualifying analyst access. Only the exact Gold table locations inside the current project's warehouse are accepted; external buckets, alternative table names and broad locations are refused. Choose a demo identifier short enough for the IAM data-role name's 64-character limit.

Apply the reviewed operator-policy addition for assuming the named analyst role and the bootstrap cleanup-policy update before enabling. The operator deliberately cannot change its own policy. Use the existing same-account administration identity for the exact reviewed policy update. The cleanup role gains only read/revoke/deregister LF API actions plus deletion of the two named ephemeral role patterns; it gains no LF grant or account-settings action. LF actions without IAM resource-level support use `*`, while delegated LF rights and the destroy-plan guard restrict concrete operations.

Then run from the repository root with the optional AWS dependency installed:

```bash
AWS_PROFILE=PROJECT_OPERATOR_PROFILE .venv/bin/python scripts/enable_analyst_access.py --var-file artifacts/demo/main.tfvars.json --artifact-dir artifacts/demo/governance --project-profile PROJECT_OPERATOR_PROFILE --governance-profile GOVERNANCE_PROFILE
```

Use an existing private variable file and a fresh artifact directory. The helper saves a private plan and accepts only create/update operations on the exact analyst resource-address set. Persistent deployment identities and unrelated changes are refused. Canonical Gold tables must already exist. The governance and project identities must belong to the same account.

If the governance principal is not already an LF administrator, the helper temporarily appends only that canonical IAM principal to `DataLakeAdmins`. It preserves all defaults and existing administrators. Inside that narrowly bounded maintenance window it applies the saved scoped plan and removes legacy `IAM_ALLOWED_PRINCIPALS` grants from the three Gold tables **only after** explicit analyst, writer, governance and cleanup rights are verified. It then removes its temporary admin entry in `finally` and verifies the exact settings restoration, including on failure. Existing administrators are never removed.

This bootstrap requires an authorized governance profile with the IAM permissions for LF setup and, when needed, `GetDataLakeSettings`/`PutDataLakeSettings`. Account-settings maintenance must be serialized because the API has no conditional update. A concurrent change is preserved rather than overwritten, our added entry is removed, and a non-restored journal is reported for review. Raw before/after settings, plans and outputs remain private; never publish them.

Retain `analyst_access_enabled=true` and the governance-profile value in subsequent private Terraform commands/variable files while the feature is active. The helper supplies these flags for its own saved plan but does not rewrite the caller's variable file. The feature defaults to disabled; an ordinary plan using the default proposes removal of this ephemeral access set. Normal updates after bootstrap use the explicit project-scoped LF delegation and do not require leaving an account-wide admin entry behind.

## Real access qualification

Save `terraform -chdir=terraform output -json` to an ignored private file. Generate an independent expected-products JSON using the local Gold calculator, containing the full arrays for all three products. Ensure a known synthetic Bronze probe object exists. Then run:

```bash
AWS_PROFILE=PROJECT_OPERATOR_PROFILE .venv/bin/python scripts/qualify_analyst_access.py --outputs artifacts/demo/outputs.json --expected artifacts/demo/expected-gold.json --bronze-probe-key landing/batch_id=analyst-fixture/bronze-probe.json --project-profile PROJECT_OPERATOR_PROFILE --governance-profile GOVERNANCE_PROFILE --report artifacts/demo/analyst-access.json
```

The report path must be fresh. Credentials are obtained through STS and kept in memory, never saved as an AWS profile/key file. The script verifies the actual assumed-role ARN, workgroup settings, exact registrations and absence of legacy Gold grants. It queries every Gold group and both measures with result reuse disabled, comparing the complete result to the local oracle. Empty expected products are refused as an insufficient positive-data proof.

For all eleven Silver/internal tables, including quarantine, the operator first confirms existence and the analyst must receive a direct Glue `AccessDeniedException`. Athena must then fail its query. Athena can deliberately mask unauthorized metadata as `TABLE_NOT_FOUND`; this is accepted only with the independent operator-existence and analyst-catalog-denial checks. A missing resource, generic query failure, expired credential or network error alone is not counted as a denial.

Bronze, Silver and direct Gold S3 probes use known existing objects confirmed readable by the operator. Analyst `GetObject` must return `AccessDenied` with HTTP 403. Missing objects and other errors do not count. To prove LF is actually enforced, the script revokes `SELECT` on one Gold table while retaining `DESCRIBE` and unchanged IAM policies, verifies that the previously successful query is denied, restores `SELECT` in `finally`, and verifies the full Gold result again.

The fixture driver `tests/glue/qualify_analyst_fixture.py` can be uploaded to the disposable project's landing prefix and launched through the existing Gold job's per-run `--scriptLocation` override with `--DATABASE`. It refuses existing canonical tables. It creates four deterministic intervals and runs the exact production Gold pricing/aggregation module to populate the products; it is an access-control fixture, not a replacement for the full Silver business qualification.

## Destruction and boundaries

The cleanup target list includes the analyst workgroup, both IAM roles and inline policies, all LF table/database/location grants and the three registrations. Tagged resources require exact demo tags and names. Untaggable LF records additionally require exact Gold table names, the project database, matching principal/catalog accounts, the correct permission set and the exact project S3 table prefix. Unknown instances, wildcards, cross-catalog pointers and broader grants fail closed.

Dependency ordering retains the cleanup role's grantable rights until other managed grants are revoked, then removes its own grants. Location grants are explicitly revoked before deregistration because deregistration alone does not remove them. LF can remove the caller's own grant but still return an access error when resolving that now-invisible resource. The destroy driver permits one recovery only when every apply error is an LF revoke error: it refreshes state, checks the complete plan again, requires a strictly smaller deletion set with no remaining LF permission revocations, then applies that saved plan. Other errors, no progress, new targets or a second failure stop cleanup. No temporary global LF administration is needed by the cleanup job. Use the tested pushed revision for branch qualification before scheduled `main` contains these addresses and permissions.

This scope proves same-account table access, not row/column/tenant filtering, cross-account sharing, IAM Identity Center, or a separate ingestion identity. Representative direct object reads are tested; this is not an exhaustive account-wide authorization audit. Freeze controlled fixture data during qualification and review sanitized evidence before publication.

## Managed qualification on 2026-11-03

The canonical fixture was produced by the deployed Gold module on Glue 5. Actual 15-minute analyst credentials then read all groups/measures from the three products and matched the independent local calculator: four daily-site rows, four daily-customer rows and two monthly-customer rows. Each product totals 6 kWh and EUR 1.20. Result reuse was disabled and reported false.

All ten existing Silver/internal tables returned direct analyst Glue access denials and failed Athena queries; masked `TABLE_NOT_FOUND` responses were accepted only after independent existence/denial verification. Known existing Bronze, Silver and direct Gold objects returned analyst S3 `AccessDenied`/403. Revoking Gold `SELECT` blocked a previously successful query while retaining `DESCRIBE`; restoration was verified with a complete result comparison. A subsequent Terraform convergence plan reported no managed changes. Temporary LF bootstrap journals restored the original account settings exactly.

The first managed cleanup exposed a missing ephemeral-role inspection action and LF self-revocation errors despite actual grant removal. After the scoped IAM fix, the pushed destroy driver removed the remaining resources successfully. A second managed run on the empty environment also succeeded. Independent checks found no analyst/data role, analyst workgroup, Gold registration, scoped grant or demo data-plane resource; deployment identities, versioned state, cleanup context and the enabled schedule were preserved. The bounded automatic recovery branch is covered by unit tests; this qualification exercised the corrected driver on the remaining resources, not a fresh full two-pass teardown. See the [sanitized evidence](evidence/analyst-access-2026-11-03.json). Local checks passed 158 Python tests and six Terraform mock scenarios, with both Terraform roots validated.

Official references: [LF permissions and legacy IAM groups](https://docs.aws.amazon.com/lake-formation/latest/dg/lf-permissions-reference.html), [registration roles](https://docs.aws.amazon.com/lake-formation/latest/dg/registration-role.html), [deregistering locations](https://docs.aws.amazon.com/lake-formation/latest/dg/unregister-location.html), and [Athena LF requirements](https://docs.aws.amazon.com/athena/latest/ug/lf-athena-user-permissions.html).

A subsequent [complete native cleanup qualification](cleanup-recovery.md) exercised the automatic two-pass branch on a fresh fully populated demonstration. Its evidence supersedes the earlier limitation above while preserving the original qualification record.
