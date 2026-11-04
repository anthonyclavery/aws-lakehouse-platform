# Iceberg day-partition benchmark

This is a separate scale experiment, not a replacement for the 200-site business fixture. The default profile contains 3,504,000 hourly intervals: 200 synthetic meters across 730 days from 2024-01-01 UTC. `interval_end` is the reading timestamp; the first interval starts one hour before that origin. Consumption is deterministic integer Wh, defined by `100 + (row_id * 37 + hour_index * 13) % 4901`. The profile has no customer/tariff data and makes no billing-business claim.

## Comparable tables

The Glue builder creates fresh `benchmark_<id>_unpartitioned` and `benchmark_<id>_daily` tables in the isolated project database. It refuses to overwrite either existing table. Both tables contain the same five columns, use Parquet/ZSTD and the same 128 MiB target file size. The baseline has no partition fields and scatters history using a deterministic hash ordering. The daily layout uses Iceberg's hidden `day(interval_end)` transform and sorts by timestamp and meter. Sixteen Spark tasks distribute each write; actual file count and size are measured, not assumed.

The builder checks the persisted tables with bidirectional `EXCEPT ALL`, retaining duplicate multiplicities. It reads actual Iceberg partition specifications, writer properties and data-file metadata before writing a completed manifest. The manifest records file counts, row counts, compressed data bytes and minimum/approximate-median/maximum file sizes. It is placed in the exact isolated `landing/batch_id=benchmark-<id>/benchmark-manifest.json` path only after validation succeeds.

The comparison measures complete layouts. Day partitioning also changes clustering, file statistics and file counts. The intentionally scattered baseline prevents incidental time ordering from standing in for a useful partition specification. This is not a one-variable causal experiment, a universal performance result, or evidence that daily small files are a good production layout.

## Measurement contract

Both Athena queries project the same columns, use the same half-open UTC timestamp range, and return count, distinct meters, total Wh and first/last reading timestamps. A local integer enumeration computes expected values for every reading in the selected day. Each result must exactly match that oracle. The default day is halfway through the generated history, representing 1/730 of it.

The runner requires engine 3, enforced workgroup settings, SSE-S3 results and a positive scan cap no larger than 1 GiB per query. It explicitly disables result reuse, then refuses any result whose service statistics do not report reuse as false. It alternates the first layout across three to ten interleaved repetitions and retains each measurement, including the first. There is no discarded warm-up run. This does not flush service/storage caches. Snapshot identifiers are bound in the private completed-build manifest and checked against the current Iceberg history before and after measurement; a changed snapshot, stale manifest, missing measurement or wrong result prevents a successful report. Keep both benchmark tables unchanged throughout the experiment.

Published measurements include individual and median bytes scanned, engine execution time, planning time, queue time and total execution time. Planning is part of engine execution, so the fields must not be added together. Results are sanitized: SQL uses `benchmark_database` in place of the private deployed database, and reports omit account/bucket/workgroup/query/snapshot identifiers.

Each query also records a scan-cost **estimate**, using $5/TB, decimal MB/TB, rounding up to 1 MB and a minimum 10 MB per query. These explicit unit/rate assumptions are a model, not an invoice. Reduced measured bytes can leave the estimate unchanged below the billing minimum. Glue compute, S3, catalog charges, four metadata-validation queries and taxes are excluded. See [Athena pricing](https://aws.amazon.com/athena/pricing/), [execution statistic definitions](https://docs.aws.amazon.com/athena/latest/APIReference/API_QueryExecutionStatistics.html) and [result reuse](https://docs.aws.amazon.com/athena/latest/ug/reusing-query-results.html).

## Deployment and execution

Terraform ships the builder and shared profile module as two tagged objects. The operator policy adds only their exact object paths. The benchmark reuses the existing two-worker G.1X, 30-minute Gold pricing job through a [per-run script override](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-glue-arguments.html); it does not change that job's default script or Gold tables. Pause Gold processing while using this shared runtime. There is no new always-on service or automatic benchmark schedule.

Deploy the tested Terraform source and obtain the `partition_benchmark`, `glue_database_name` and `athena_workgroup_name` outputs. Supply those values to the runner:

```bash
.venv/bin/python scripts/run_partition_benchmark.py --database DEMO_DATABASE --workgroup DEMO_WORKGROUP --glue-job DEMO_GOLD_PRICE_JOB --script-uri s3://DEMO_BUCKET/jobs/benchmark_partition_pruning.py --module-uri s3://DEMO_BUCKET/jobs/partition_benchmark.py --benchmark-id pruning_v1 --profile PROJECT_OPERATOR_PROFILE --output artifacts/pruning/report.json --dry-run
```

Replace every uppercase placeholder with the deployment output; remove `--dry-run` to build and measure. Identifiers, script paths, resource namespace and profile envelope are checked before launch. The exact project database/job names are required. The defaults are 730 days, 200 meters and three repetitions; the hard envelope is 1,096 days, 1,000 meters and 20 million total rows. A smaller explicitly recorded profile can be used for a smoke test.

Never overwrite the output report. Use a fresh benchmark identifier after a partial build; existing tables remain intact for diagnosis. If a build succeeded and measurement failed, use `--measure-only` with the same profile/identifier and a fresh output path to measure the completed manifest without rebuilding. Inspect active jobs after client timeouts before retrying. A timed-out Athena query is canceled by the shared query helper.

## Cleanup and limits

Both new object addresses join the exact tag-checked cleanup allowlist and the cleanup target list. Tables, manifests and data files disappear with the project database/bucket; identities and Terraform state remain protected. Before relying on scheduled cleanup from `main`, merge/deploy the source that understands these new object addresses. Branch qualification must use its tested pushed revision for guarded cleanup.

This experiment measures one selective query, one region, one data pattern and one pair of layouts. It does not demonstrate fragmented-file compaction, large-scale ingest throughput, end-user latency or Lake Formation analyst isolation. The latter requires a separately reviewed Lake Formation administration and access configuration; this benchmark does not modify account-wide Lake Formation settings.

## Measured campaign on 2026-11-03

The final `pruning_v2` profile ran in eu-west-3 on Glue 5 with two G.1X workers. Building and validating the two 3,504,000-row tables took 127 service execution seconds. The half-open query selected 2024-12-31 UTC and returned exactly 4,800 rows, 200 meters and 12,207,757 Wh in all six measurements. Result reuse was false and both snapshots matched their build manifest throughout the campaign.

| Measurement | Unpartitioned/scattered | Daily/time-clustered |
| --- | ---: | ---: |
| Data files | 10 | 730 |
| Compressed data bytes | 23,589,459 | 7,157,443 |
| Median query bytes scanned | 17,005,513 | 8,032 |
| Median engine execution, ms | 756 | 591 |
| Median planning, ms | 93 | 99 |
| Median queue, ms | 62 | 61 |
| Median total execution, ms | 911 | 745 |
| Historical modeled scan charge at USD 5/TB | 0.00009 | 0.00005 |

The scan reduction is 99.9528% for this selective query and these two layouts. The corresponding modeled scan-charge reduction is 44.4%, because the daily query reaches the 10 MB minimum. The six measured queries total USD 0.00042 under the documented model; this excludes provisioning, Glue, storage, other validation calls and taxes. It is not a measured bill. The many tiny daily files and different compression ratio are material caveats. Three samples per layout cannot establish stable latency or a universal speedup.

All samples, SQL templates, layout metadata, source hashes and teardown checks are in [the reviewed evidence](evidence/partition-pruning-2026-11-03.json). The final source passed 124 local tests and four mocked Terraform plans. Guarded CodeBuild cleanup ran from the tested pushed revision; direct checks confirmed the demo bucket/database, four jobs and Athena workgroup were absent, while deployment identities, versioned state and the enabled cleanup schedule remained. Raw logs, manifest snapshot identifiers and deployment configuration stay in ignored private artifacts.

The [final cost report](cost-and-destruction.md) updates the regional rate to the Paris catalog observation of USD 7/TB on 2026-11-04 and recomputes the same examples. Historical measurements and their original explicit USD 5/TB model are preserved; this is not another benchmark execution.
