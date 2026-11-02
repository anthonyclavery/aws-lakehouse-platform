# AWS pipeline qualification

## Scope

Qualification checks the deployed Glue/Iceberg pipeline against the local calculator on controlled synthetic batch history. It covers reference loading, interval derivation, pricing, the three Gold products, incremental data, a late reading, a higher-version correction and unchanged replay. The calculator is shared between local and Glue execution; business-boundary unit tests validate its rules separately. This comparison validates the deployed adapters, storage and aggregation rather than providing an independent tariff implementation.

It does not qualify a separate analyst identity or Lake Formation denial rules. The operational Terraform role can ingest landing objects, read warehouse objects and query the project catalog. That role is not the future Gold-only analyst role. Performance measurements on this small fixture are execution evidence, not a scaling benchmark. Silver currently recalculates the complete history of each affected meter; Gold rebuilds complete snapshots. This test does not demonstrate bounded backfill performance.

## Measured qualification on 2 November 2026

The [reviewed JSON evidence](evidence/aws-qualification-2026-11-02.json) records the live `eu-west-3` run with five synthetic customers, ten sites and all three contracts (5 BASE, 4 WEEKEND, 1 PEAK_OFF_PEAK). It contains no AWS account, bucket, database or query identifiers.

| Snapshot | Daily site groups | Daily customer groups | Monthly customer groups | Athena/local result |
| --- | ---: | ---: | ---: | --- |
| Initial | 4,943 | 2,307 | 81 | Exact fingerprints; zero measure error |
| Incremental | 4,953 | 2,312 | 81 | Exact fingerprints; zero measure error |
| Late + corrected | 4,953 | 2,312 | 81 | Exact fingerprints; zero measure error |
| Initial replay after corrections | 4,953 | 2,312 | 81 | Exact fingerprints; zero measure error |

All 16 managed Glue runs succeeded. The initial 53,900 indexes produced 53,890 intervals. Adding 125 indexes, one late reading and one higher-version correction yielded 54,026 distinct index keys and 54,016 intervals. The correction remained at version 2 after replay. The valid fixture had zero quarantined records; this does not demonstrate managed negative-input quarantine behavior.

Incremental and exception snapshots changed the daily product fingerprints, while replay left every corrected product fingerprint unchanged. The four three-product reconciliations scanned 2,987,205 bytes in total. These are measured scan bytes, not Athena billed bytes, which have minimum charging units.

The first guarded CodeBuild run removed the 17 reviewed data-plane resources. Direct AWS checks confirmed absence of the bucket, database, four jobs, workgroup and execution inline policy. Deployment identities, the Glue role, versioned state bucket and cleanup context remained. The initial empty repeat exposed Terraform's omitted change-list encoding and failed closed; after the narrowly tested fix, CodeBuild repeated cleanup successfully with zero changes. Raw logs and plans remain private; source revisions and outcomes are in the evidence JSON. Control-plane resources and shared service logs are retained.

Qualification also fixed the missing encryption permission, missing generated batch hash manifests, and the Glue capacity-release collision between sequential reference loads. No raw source records, AWS account identifiers or credentials were published.

## Runtime and permissions

Plan and review both Terraform roots before deployment. Use the dedicated project profile for provisioning and job/query execution. Changes to that role's own policy require the same-account administration profile to apply the exact reviewed policy once; the dedicated role intentionally cannot grant itself permissions. Persistent deployment identities and the state bucket remain outside demonstration cleanup.

The operator needs the explicit `s3:PutEncryptionConfiguration` action to configure bucket encryption; `s3:PutBucket*` does not cover that action. Landing ingestion is restricted to the project `landing/` prefix. Warehouse access for qualification is read-only. Athena result writes are restricted to the project `athena-results/` prefix.

`terraform/athena.tf` provisions an isolated workgroup with engine version 3, enforced configuration, SSE-S3 results and a 1 GiB scan cutoff per query. The `athena_workgroup_name` output selects it. The workgroup carries required demo tags and is included in guarded cleanup together with its results bucket. Bootstrap cleanup permissions must be updated before relying on scheduled cleanup.

Reference/Gold launchers enable native Glue job-run queuing, preserving the one-run concurrency limit while AWS releases a completed run's capacity. `WAITING` is monitored within the existing client time bound and `EXPIRED` fails the chain. The optional AWS dependency floor includes that request parameter. See [the Glue StartJobRun API](https://docs.aws.amazon.com/glue/latest/webapi/API_StartJobRun.html).

## Reconciliation command

After the [reference](glue-reference-job.md), Silver index and [Gold](glue-gold-jobs.md) stages succeed, supply the complete batch history to the comparator:

```bash
.venv/bin/python scripts/qualify_gold_athena.py artifacts/initial artifacts/incremental artifacts/correction artifacts/late --database demo_database --workgroup DEMO_WORKGROUP --profile PROJECT_OPERATOR_PROFILE --region eu-west-3 --output artifacts/qualification/report.json
```

Replace database/workgroup/profile placeholders with the deployment outputs and configured profile. Omit batches not yet ingested. Generated initial, incremental, correction and late batches all include file hashes accepted by the immutable Bronze uploader. The initial index source is a partitioned `consumption_index/` directory; incremental and exception batches contain root JSONL files. Pass the exact index path to the Silver runner, excluding manifests and reference files.

The comparator reads every group from daily site, daily customer and monthly customer products through paginated Athena results. It compares both measures, rejects duplicate groups and non-finite values, and checks missing/extra groups. Expected segments are rounded half-even to the deployed 12-decimal scale before local aggregation. The absolute per-group tolerance is `1e-9` kWh/EUR, and maximum observed errors are reported. Deterministic SHA-256 fingerprints additionally support exact replay comparisons; a fingerprint difference can still be within the numeric tolerance.

Query failures, cancellation, timeouts or reconciliation differences return a nonzero exit status. A timed-out query is stopped. Reports contain table names, counts, errors, fingerprints and scan/runtime statistics; they omit account, bucket, database and query identifiers. Raw deployment logs, plans, states and job journals remain private under ignored `artifacts/` paths.

## Evidence sequence

Load all four reference flows, then process and reconcile initial indexes. Append an incremental batch and reconcile again. Apply both a late reading and a higher-version correction, then reconcile their combined state. Finally replay the initial immutable batch and verify that the corrected Gold fingerprints remain unchanged. Check Silver index/interval counts and quarantine independently through Athena before recording success.

After capturing reviewed summaries, generate a targeted destroy plan using the data-plane addresses in `scripts/cleanup/buildspec.yml`, validate it with `scripts/cleanup/verify_destroy_plan.py`, then apply that exact saved plan. Verify absence of the demo bucket, Glue database/jobs and Athena workgroup, and verify that persistent identities/state remain. When qualification uses branch changes that are not yet merged, perform this guarded cleanup from the tested checkout instead of relying on the scheduler's `main` source.

Official engine reference: [Athena Iceberg support](https://docs.aws.amazon.com/athena/latest/ug/querying-iceberg.html).
