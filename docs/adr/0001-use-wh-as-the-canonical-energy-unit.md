# ADR 0001: Use watt-hours as the canonical energy unit

## Status

Accepted.

## Context

Meter indexes and interval consumption must be represented without floating-point rounding ambiguity. Analyst-facing data remains easier to read in kilowatt-hours.

## Decision

Bronze and silver records use integer watt-hours (`Wh`) for `meter_index_wh` and `consumption_wh`. Gold products derive decimal `kWh` by dividing by 1,000. Tariffs remain expressed in euros per `kWh`.

## Consequences

Index differences, rollover calculations, and synthetic generation remain exact integers. The project must convert units explicitly when calculating or displaying energy cost.
