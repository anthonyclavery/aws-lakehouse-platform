# Benchmark methodology

## Scope

The functional dataset demonstrates business correctness. A separate scale profile is used only for benchmarks so that scan and file-layout effects are measurable without altering the 200-site domain scenario.

## Partition pruning

The same interval dataset is loaded into two comparable Iceberg tables. The reference table has no useful temporal partitioning. The optimized table uses a day-level time partition. A fixed Athena query filters a short time range within the multi-year history.

The report records the exact SQL, table properties, dataset profile, region, Athena engine, workgroup settings, query time, bytes scanned, and estimated scan cost. Query result reuse is disabled. Each query is executed at least three times and individual values plus a median are published.

The executable [partition benchmark](partition-benchmark.md) implements this comparison. Its baseline scatters history through unpartitioned files; its optimized layout clusters rows by time within day partitions. Results describe these two complete layouts, including their different file counts and statistics, rather than isolating one engine setting. Both tables must pass a bidirectional `EXCEPT ALL` before a manifest is published. Every measured result is also compared with an independent integer oracle, and snapshots must remain unchanged during measurement.

## Small files and compaction

The same benchmark dataset is written first through intentionally fragmented micro-batches. The query is measured before maintenance, then Iceberg file rewrite maintenance is performed and the identical query is measured again.

The report records file count, size distribution, query time, bytes scanned, and estimated scan cost. The report does not claim a universal performance gain; it reports only the measured outcome for the published configuration.

The executable [same-table compaction benchmark](compaction-benchmark.md) implements this sequence, including a durable pre-compaction measurement checkpoint, recovery after an interrupted rewrite commit, full before/after multiset verification and explicit retention of the original snapshot. It distinguishes active-file reduction from physical S3 reclamation and records sequential-phase cache/time-order limitations.

## Publication standard

No benchmark result is committed until it has been executed and independently reviewed. The raw evidence is redacted for public safety before screenshots or summaries are added to the repository.
