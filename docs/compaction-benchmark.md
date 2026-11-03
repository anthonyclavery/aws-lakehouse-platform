# Same-table Iceberg compaction benchmark

This experiment measures one table before and after `rewrite_data_files`, preserving its partition specification, schema and writer properties. It complements the [partition-layout comparison](partition-benchmark.md). The synthetic profile is designed to expose small-file overhead rather than represent production ingestion throughput.

## Fixture and rewrite

Defaults are 144,000 intervals: 200 synthetic meters, 30 UTC days from 2024-01-01 and 16 disjoint micro-batches. The deterministic integer Wh formula and independent daily oracle are shared with the partition benchmark. Each batch contains readings across the history and appends small files to day partitions. No existing table is overwritten. The isolated name is `benchmark_<id>_compaction`; a partial build requires a fresh identifier.

The table uses hidden `day(interval_end)` partitioning, Parquet/ZSTD, a 128 MiB target and `write.distribution-mode=none`. The builder verifies the complete persisted multiset against the generated fixture in both directions with `EXCEPT ALL`, including duplicate multiplicities. File count, compressed data bytes and minimum/approximate-median/maximum file sizes come from active Iceberg data-file metadata.

The compactor calls [Iceberg's Spark procedure](https://iceberg.apache.org/docs/1.7.1/spark-procedures/#rewrite_data_files) with `strategy=binpack`, `rewrite-all=true`, `min-input-files=2`, a 128 MiB target, at most two simultaneous file-group rewrites and `partial-progress.enabled=false`. Partition boundaries still limit how large a compacted file can become. There is one intended rewrite commit. There is no sort-strategy change, manifest rewrite, snapshot expiration or orphan-file deletion.

After rewriting, the job reads the original snapshot with Iceberg time travel and compares its full schema and multiset to the current table. It verifies unchanged partition and writer properties and a reduction in active file count. A count/sum-only check would not be sufficient to publish a successful compaction manifest.

## Measurements and durable recovery

The launcher builds the table, takes three to ten baseline Athena measurements, persists them locally, launches compaction, then repeats the identical SQL on the same table. Both phases use the same selected day, workgroup configuration and exact integer oracle. Each phase is bound to its completed manifest's snapshot, checked through current Iceberg history before and after querying. Every query must return the expected row count, distinct meter count, Wh sum and first/last timestamps.

Engine 3, enforced settings, SSE-S3 results and a positive scan cap no larger than 1 GiB are required. Query-result reuse is explicitly disabled and must be reported false. Individual samples and medians record bytes scanned, engine, planning, queue and total times. Planning is a subset of engine time. Before queries necessarily precede after queries; the experiment cannot interleave phases or eliminate time-order, service-cache and storage-cache bias. No warm-up sample is discarded and no speedup is assumed.

Completed build/compact manifests live under `landing/batch_id=compaction-<id>/`. They retain private snapshot identifiers. Local `<output>.build.private.json` and `<output>.compact.private.json` files preserve phase measurements and their database/workgroup/region context. Final public reports remove those identifiers and use `benchmark_database` in SQL templates. The exact same output path and profile must be retained on retry; files are never silently overwritten.

If the compaction commit succeeded before the driver stopped, retry accepts only an unchanged baseline or its direct `replace` child. The former runs the rewrite; the latter skips rewriting and verifies the retained original snapshot against that child before publishing the missing manifest. Any intervening append, overwrite or different lineage is refused. A pre-existing completed compaction manifest is also checked against current state. Saved phase measurements must match the profile, snapshot, day, repetition count and internally consistent samples. The final compacted snapshot is checked once more before report publication.

Run a single writer across this experiment and keep both snapshots readable until all proofs and measurements finish. The per-job Glue concurrency limit does not implement a global lock across other writers. A successful return from the rewrite alone is not a complete benchmark result.

## Deployment and execution

Terraform adds two tagged artifacts and the `compaction_benchmark` output. Both benchmark types reuse the existing two-worker G.1X, 30-minute Gold pricing runtime through an explicit per-run script override; normal Gold defaults and production table names are unchanged. Pause Gold runs while using this shared job. The shared deterministic fixture/metadata helpers remain available to the pruning benchmark.

After deploying the tested source, use the output job/script/module, database and workgroup values:

```bash
.venv/bin/python scripts/run_compaction_benchmark.py --database DEMO_DATABASE --workgroup DEMO_WORKGROUP --glue-job DEMO_GOLD_PRICE_JOB --script-uri s3://DEMO_BUCKET/jobs/benchmark_compaction.py --module-uri s3://DEMO_BUCKET/jobs/compaction_benchmark.py --benchmark-id compact_v1 --profile PROJECT_OPERATOR_PROFILE --output artifacts/compaction/report.json --dry-run
```

Replace every uppercase placeholder and remove `--dry-run` to execute. The hard profile envelope is 90 days, 500 meters and one million rows, with 2-32 micro-batches no greater than the meter count. A smaller smoke profile must state all parameters. There is no automatic schedule, retry or compaction of unrelated tables.

After inspecting the stopped/failed run, append `--resume` to the **same command and output path**. Completed baseline measurements are reused, not remeasured after compaction. Missing pre-compaction measurements cannot be reconstructed as if they had happened earlier. Context changes, incomplete measurements, stale manifests or unexpected history return a nonzero status. Athena query timeouts are canceled; Glue client wait timeouts require inspecting the still-active job before retrying.

For controlled qualification only, `--inject-post-rewrite-failure` stops a newly executed compaction immediately after its commit, before verification and manifest publication. This intentionally makes that Glue run fail. Retrying with `--resume` and without the injection flag must recover the existing commit, preserve the original measurements and skip a second rewrite. Verify the exact injected error rather than accepting an arbitrary failed job as expected.

## Cost, cleanup and interpretation

Reports retain the explicit scan-cost model from the partition benchmark: $5/TB with decimal MB/TB, 1 MB rounding and a 10 MB minimum per query. The minimum can make before/after estimates identical even when measured bytes fall. Estimates exclude Glue (including failed runs), storage, catalog, metadata verification queries and taxes. They are not an AWS bill.

Only active-file layout is measured. The original files remain reachable through retained snapshots, so active-file reduction is not immediate S3 space reclamation or a storage-cost saving. Compaction can also change clustering, compression and file statistics. One synthetic fixture and a few samples do not prove a universal latency improvement.

The exact two artifact addresses join the tag-checked cleanup allowlist. The existing benchmark target removes them; the database/bucket remove active files, retained snapshots, manifests and partial builds. Qualification cleanup must use the tested pushed branch revision before `main` contains these new addresses. Deployment identities, versioned state and the cleanup control plane stay protected. No wider warehouse/catalog grant or account-wide Lake Formation change is introduced.

## Measured campaign on 2026-11-03

The default `compact_v1` profile ran on Glue 5 with two G.1X workers in eu-west-3. Building the fragmented table took 145 service execution seconds. The deliberate post-commit interruption produced the expected failed Glue run after 73 seconds; the recovery job then succeeded in 110 seconds. Its manifest confirms reuse of the existing direct rewrite commit, not another rewrite. The baseline measurement checkpoint remained byte-for-byte unchanged.

All six measured queries selected 2024-01-16 UTC and returned exactly 4,800 readings, 200 meters and 12,272,024 Wh. Reuse was false. Full bidirectional multiset/schema comparison passed between the retained original snapshot and the compacted snapshot; partition/writer properties remained identical.

| Measurement | Fragmented | Compacted |
| --- | ---: | ---: |
| Active data files | 480 | 30 |
| Active compressed data bytes | 1,452,875 | 352,916 |
| Minimum file bytes | 2,999 | 11,728 |
| Approximate median file bytes | 3,028 | 11,766 |
| Maximum file bytes | 3,051 | 11,795 |
| Median query bytes scanned | 20,399 | 9,955 |
| Median engine execution, ms | 674 | 629 |
| Median planning, ms | 93 | 103 |
| Median queue, ms | 55 | 67 |
| Median total execution, ms | 789 | 791 |
| Modeled scan charge per measured query, USD | 0.00005 | 0.00005 |

Active-file count fell by 93.75%, but total query latency was effectively unchanged and the modeled query charge did not change. The six measured queries total USD 0.00030 under the stated assumptions. The retained original files were still readable for verification, so the active-byte reduction is not a claim of reclaimed S3 storage. These results describe this small synthetic fixture and sequential sample set only.
