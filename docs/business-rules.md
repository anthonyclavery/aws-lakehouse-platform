# Business rules

## Cumulative indexes

`consumption_index` carries a cumulative meter register in `Wh`, not consumption for a period. A validated index closes the consumption period from the previous index of the same meter.

For ordinary readings, consumption is the current index minus the previous index. The resulting interval retains its start and end timestamps, source indexes, quality status, and allocation method.

## Meter rollovers

All V1 meters use the synthetic maximum register value `999999000 Wh`. If an index becomes lower than its predecessor, the record is valid only when `rollover_count` is positive. For one rollover, consumption is:

```text
((999999000 + 1) - previous_meter_index_wh) + current_meter_index_wh
```

An unexplained decrease is quarantined.

## Meter replacement

A meter replacement closes the prior `site_meter` relationship and opens a new one at a precise timestamp. The first index of the new meter is marked `INSTALLATION_BASELINE` and produces no consumption. The next index creates the first consumption interval for the replacement meter.

This first interval may be shorter or longer than the standard hourly or daily cadence. It is valid but marked irregular. If it crosses tariff windows, energy is allocated proportionally to elapsed time and recorded with `allocation_method = PRO_RATA_TIME`.

## Contracts and tariffing

`BASE` has one daily synthetic energy price. `WEEKEND` uses separate weekday and weekend prices. `PEAK_OFF_PEAK` uses hourly local-time windows, including a window that may cross midnight.

Contract changes are effective at local midnight. A transition index is generated at the change boundary whenever the expected reading cadence changes.

Prices cover energy only. The project deliberately excludes subscriptions, tax, network costs, VAT, and invoice calculation.

## Late data, corrections, and backfills

An unchanged replay is ignored. A higher `source_version` is a correction. A late index or corrected index recalculates the interval immediately before and after that index, then refreshes only affected gold aggregates.

Changes to customer ownership, site-to-meter relationships, contracts, or tariffs trigger bounded recalculation for their effective periods. A quarantine record never reaches gold tables.

## Quality policy

Records are `VALID`, `VALID_WITH_WARNING`, or `QUARANTINED`. Late data, declared rollovers, baseline readings, replacements, and irregular intervals are valid situations with traceable warnings. Invalid identifiers, missing required fields, negative indexes, unexplained regressions, overlapping active relationships, and uncovered tariff periods are quarantined.
