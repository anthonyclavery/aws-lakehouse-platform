# Dedicated Bronze ingestion identity

The optional `<demo>-ingestion` IAM role separates source publication from transformation, analyst queries and Terraform administration. Only the dedicated project Terraform operator can assume it. The uploader obtains a 15-minute STS session in memory; no new IAM user, static key, stored profile or credential file is created. The role's maximum session duration is one hour.

## Effective boundary

| Operation | Scope |
| --- | --- |
| Locate bucket | The exact demonstration bucket |
| List objects | Only prefixes matching `landing/batch_id=*/*`; bucket and landing-root listings are refused |
| Read objects | Only current objects under that bucket's batch prefixes, for full-byte replay verification |
| Create objects | Only those batch prefixes, with `s3:if-none-match = *` |
| Overwrite or delete | Unconditional writes and object/version deletion are explicitly denied; conditional replacement fails with S3 HTTP 412 |
| Other objects | Direct reads/writes outside that exact bucket's batch prefixes are explicitly denied, including warehouse and job artifacts |
| Control plane | Glue, Athena, Lake Formation and IAM actions are explicitly denied; the role has no permission to assume another role |

This role has no LF grant and does not need one to publish raw S3 objects. Gold registration and writer grants remain separate. Permissions apply across batches in one demo, not per customer, tenant or batch owner. IAM does not validate JSON schemas or manifest contents: use the [manifest-backed conditional uploader](landing-ingestion.md). Cleanup remains a different identity with deliberate deletion permissions. This is not account-wide Object Lock or protection against another authorized writer/deleter.

The write condition is enforced by the ingestion role's IAM policy in addition to the uploader's conditional requests. It covers direct calls too: omitting `If-None-Match: *` is denied, even for a fresh key. Multipart ingestion is not enabled. See the AWS [conditional-write condition keys](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html).

## Enable and use

First apply the reviewed `AssumeOnlyProjectIngestion` addition to the persistent project operator policy using the existing administration identity. Apply the bootstrap cleanup-policy extension permitting deletion of only the named ephemeral ingestion role pattern. Neither update gives the ingestion role administration access. Do not replace persistent deployment identities or change their configured identity.

Set `ingestion_access_enabled=true` and `ingestion_governance_profile=<same-account-admin-profile>` in a private Terraform variable file, then review and apply its saved plan. The feature defaults to disabled. The aliased governance provider creates only the tagged role and its inline policy; it must be in the project account. Use a demo ID no longer than 31 characters to keep the IAM role name within 64 characters. Keep the flag/profile in subsequent private plans while the role is active, alongside any enabled analyst flags. A default-disabled plan proposes removing the role.

Save `terraform -chdir=terraform output -json` privately and read `ingestion_access.role_arn`. Run the existing uploader through the project operator profile:

```bash
python scripts/upload_landing_batch.py \
  --batch-dir artifacts/initial \
  --batch-id initial-unique-run \
  --bucket <lakehouse-bucket> \
  --profile lakehouse-deployer \
  --region eu-west-3 \
  --role-arn <ingestion-role-arn>
```

The role and bucket must identify the same demo/account, and the source profile must actually assume the dedicated project Terraform operator. `--dry-run` validates the batch locally without contacting AWS. Omitting `--role-arn` retains the prior profile-based uploader for compatibility; use the role option to exercise the dedicated boundary.

## Actual access qualification

Create canonical Silver/Gold tables and files using the existing transformation fixture before negative tests. Use a fresh synthetic batch ID and report path:

```bash
python scripts/qualify_ingestion_access.py \
  --outputs artifacts/demo/outputs.json \
  --project-profile lakehouse-deployer \
  --batch-dir artifacts/initial \
  --batch-id ingestion-access-unique-run \
  --region eu-west-3 \
  --report artifacts/demo/ingestion-access.json
```

The qualifier verifies the actual assumed-role identity, uploads and replays every object, compares stored bytes and checks scoped listing. It verifies refusal of fresh/unconditional writes, overwrite and deletion, plus the native conditional-overwrite HTTP 412 and preservation of the original bytes. Existing Silver/Gold files and the deployed job script are independently confirmed using the operator before ingestion read/write refusals count as proof. Existing Glue database/table/job, Athena workgroup and writer role are checked before service and role-assumption denials. Missing objects, expired credentials, network failures and unrelated errors cannot count as authorization denials. The report contains counts and sanitized authorization results, never infrastructure pointers or credentials.

The controlled cleanup includes both exact ingestion resource addresses. The role requires the expected demo tags/name, and the inline policy must name that exact role and policy. Other demos, missing tags, persistent-role pointers and extra instances are refused by the deletion-plan guard. Use the pushed feature revision for qualification cleanup until it is merged into scheduled `main`.

## Managed result on 2026-11-03

Actual 15-minute ingestion credentials uploaded five objects, listed only their batch prefix, compared all stored bytes and reused all five on replay. The existing uploader CLI also succeeded with `--role-arn`. Conditional overwrite returned HTTP 412; unconditional create/overwrite and deletion were refused, with the original object preserved and no unconditional object created.

Nineteen authorization denials covered those mutations, root/landing/warehouse listing, direct reads and writes against existing Silver/Gold files and the deployed job script, Glue metadata/job access, Athena workgroup access, LF settings, writer-role metadata and assuming the writer role. Missing resources were not accepted as access denials. A subsequent Terraform plan had no managed changes. The fixture used actual managed Glue output; it was not an empty-prefix permission simulation.

The pushed revision's CodeBuild cleanup succeeded in one pass with 24 deletions. Independent checks verified the ingestion role and inline policy absent, along with the demo data plane, while persistent identities, versioned state, cleanup context, LF settings and the enabled schedule were preserved. Local checks passed 178 Python tests and nine Terraform mock scenarios; both Terraform roots validated. See the [sanitized evidence](evidence/ingestion-access-2026-11-03.json).
