# Glue reference-data job

`jobs/silver_process_reference.py` loads one immutable Bronze reference flow into one Iceberg history table. Runtime arguments select the landing path, target table and business-key columns. The job is invoked once for each V1 flow: `site_meter`, `customer_site`, `site_contract` and `tariff_grid`.

The job keeps the highest `source_version`, with `received_at` as its deterministic tie-breaker. A higher version updates the matching business key through Iceberg `MERGE INTO`; an unchanged replay has no effect.
