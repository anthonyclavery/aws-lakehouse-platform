# ADR 0002: Model meter indexes as cumulative registers

## Status

Accepted.

## Context

The incoming meter flow represents a displayed cumulative index, not consumption for a reporting period. Consumption must therefore be inferred from consecutive valid readings.

## Decision

`consumption_index` stores cumulative meter registers. Silver derives `meter_consumption_interval` from adjacent indexes of the same meter. A declared rollover uses the fixed synthetic register capacity. An unexplained lower index is quarantined.

## Consequences

Late or corrected indexes can affect the interval immediately before and after the reading. The pipeline requires targeted recalculation rather than append-only aggregation.
