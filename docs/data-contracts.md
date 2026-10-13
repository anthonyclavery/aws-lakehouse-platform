# Data contracts

## Shared conventions

All business identifiers are synthetic UUIDs. Timestamps are stored in UTC. Business-time tariff interpretation uses `Europe/Paris`. Temporal validity uses the half-open interval `[valid_from, valid_to)`.

`source_version` increases when the same business record is corrected. `received_at` records when the platform receives that version. `schema_version` identifies the contract shape. Replaying an unchanged version is idempotent.

Watt-hours (`Wh`) are the canonical technical energy unit. `kWh` is derived for analyst-facing products by dividing by 1,000.

## `consumption_index`

| Field | Logical type | Meaning |
| --- | --- | --- |
| `meter_id` | UUID | Meter that emitted the index |
| `reading_at` | UTC timestamp | Time of index reading |
| `meter_index_wh` | Integer | Cumulative raw meter index in Wh |
| `rollover_count` | Integer | Meter rollovers since the previous index, zero by default |
| `reading_context` | Enum | `NORMAL` or `INSTALLATION_BASELINE` |
| `source_version` | Integer | Increasing version for `meter_id + reading_at` |
| `received_at` | UTC timestamp | Platform receipt time |
| `schema_version` | Integer | Data-contract version |

The business key is `meter_id + reading_at`.

## `site_meter`

| Field | Logical type | Meaning |
| --- | --- | --- |
| `site_id` | UUID | Consumption site |
| `meter_id` | UUID | Meter attached to the site |
| `valid_from` | UTC timestamp | Relationship start |
| `valid_to` | UTC timestamp, nullable | Relationship end |
| `change_reason` | Enum | `INITIAL_INSTALL`, `METER_REPLACEMENT`, or `CORRECTION` |
| `source_version` | Integer | Record version |
| `received_at` | UTC timestamp | Platform receipt time |
| `schema_version` | Integer | Data-contract version |

The business key is `site_id + valid_from`. A site has one active meter at a time.

## `customer_site`

| Field | Logical type | Meaning |
| --- | --- | --- |
| `customer_id` | UUID | Synthetic account holder |
| `site_id` | UUID | Consumption site |
| `valid_from` | UTC timestamp | Relationship start |
| `valid_to` | UTC timestamp, nullable | Relationship end |
| `relationship_type` | Enum | `PRIMARY_HOLDER` in V1 |
| `source_version` | Integer | Record version |
| `received_at` | UTC timestamp | Platform receipt time |
| `schema_version` | Integer | Data-contract version |

The business key is `site_id + valid_from`. A customer may hold multiple sites. A site may be vacant but has no more than one active primary holder.

## `site_contract`

| Field | Logical type | Meaning |
| --- | --- | --- |
| `contract_id` | UUID | Stable contract identifier |
| `site_id` | UUID | Covered site |
| `contract_type` | Enum | `BASE`, `WEEKEND`, or `PEAK_OFF_PEAK` |
| `valid_from` | UTC timestamp | Contract start |
| `valid_to` | UTC timestamp, nullable | Contract end |
| `source_version` | Integer | Record version |
| `received_at` | UTC timestamp | Platform receipt time |
| `schema_version` | Integer | Data-contract version |

The business key is `contract_id`. A site has one active contract at a time. Contract changes take effect at local midnight.

## `tariff_grid`

| Field | Logical type | Meaning |
| --- | --- | --- |
| `tariff_rule_id` | UUID | Stable tariff-rule identifier |
| `contract_type` | Enum | Contract type to which the rule applies |
| `valid_from` | UTC timestamp | Rule effective start |
| `valid_to` | UTC timestamp, nullable | Rule effective end |
| `day_scope` | Enum | `ALL_DAYS`, `WEEKDAY`, or `WEEKEND` |
| `time_from_local` | Local time, nullable | Local tariff-window start |
| `time_to_local` | Local time, nullable | Local tariff-window end |
| `price_eur_per_kwh` | Decimal | Synthetic energy price |
| `source_version` | Integer | Record version |
| `received_at` | UTC timestamp | Platform receipt time |
| `schema_version` | Integer | Data-contract version |

The business key is `tariff_rule_id`. Valid rules provide complete, non-overlapping coverage for each active contract type.
