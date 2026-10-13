# ADR 0006: Partition analytical Iceberg tables by day

## Status

Accepted.

## Context

The primary analytical questions filter energy consumption by time range. The project also needs a meaningful partition-pruning benchmark.

## Decision

Time-oriented analytical Iceberg tables are partitioned by day using the interval date. The benchmark compares this design against a reference table without useful temporal partitioning.

## Consequences

Athena can prune unrelated daily partitions for bounded date queries. The project must document the physical layout and verify the observed result rather than claim a universal performance improvement.
