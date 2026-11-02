# Glue reference-data job

`jobs/silver_process_reference.py` loads one immutable Bronze reference flow into one Iceberg history table. Runtime arguments select the landing path, target table and business-key columns. The job is invoked once for each V1 flow: `site_meter`, `customer_site`, `site_contract` and `tariff_grid`.

The job keeps the highest `source_version`, with `received_at` as its deterministic tie-breaker. A higher version updates the matching business key through Iceberg `MERGE INTO`; an unchanged replay has no effect.

## Provisioning

`terraform/glue_reference.tf` uploads the existing script and provisions one reusable Glue 5.0 job. It shares the Silver transformation role, bucket, database and Iceberg configuration. Each invocation requires explicit `LANDING_PATH`, `TARGET_TABLE` and `BUSINESS_KEY_COLUMNS` arguments. Bookmarks are disabled so immutable batches can be replayed. Two G.1X workers, a 30-minute timeout, zero retries and one concurrent run bound the demonstration.

The `silver_reference_job_name` and `glue_database_name` Terraform outputs provide the runtime identifiers. The operator may manage the named job/artifact and start/read all four pipeline jobs. Scheduled cleanup includes the reference job and tagged script in its exact allowlist; both require the current demo tags. Apply the bootstrap permission update before relying on that cleanup. No schedule starts processing automatically.

## Run the four flows

Upload a manifest-backed initial batch with `scripts/upload_landing_batch.py`. It preserves paths under `s3://BUCKET/landing/batch_id=BATCH_ID/`. From the repository root, preview all reference invocations offline:

```bash
.venv/bin/python scripts/run_silver_reference_jobs.py --job-name DEMO_REFERENCE_JOB --database demo_database --batch-path s3://aws-lakehouse-platform-example/landing/batch_id=initial-demo --dry-run
```

Replace `DEMO_REFERENCE_JOB`, `demo_database` and the example S3 location with the deployed output values and uploaded batch. Remove `--dry-run` and add `--profile PROJECT_OPERATOR_PROFILE --region eu-west-3` for an explicit AWS run. That requires the optional AWS Python dependency and a configured operator profile.

| Flow | Exact source suffix | Target table suffix | Business key |
| --- | --- | --- | --- |
| site_meter | reference/site_meter.jsonl | site_meter_history | site_id, valid_from |
| customer_site | reference/customer_site.jsonl | customer_site_history | site_id, valid_from |
| site_contract | reference/site_contract.jsonl | site_contract_history | contract_id |
| tariff_grid | reference/tariff_grid.jsonl | tariff_grid_history | tariff_rule_id |

The runner always targets `glue_catalog.<database>.<table>`. It never reads the whole batch or mixes reference flows. It validates the batch URI, database and flow selection before starting any run. Default order is the table order above. Each run must reach `SUCCEEDED` before the next starts. API failure, terminal failure, expiry or a one-hour client wait timeout stops the chain with a nonzero exit status. A client timeout leaves the AWS run active; inspect its printed run ID before retrying. Completed earlier flows remain committed and may be replayed.

For a partial reference correction, select only the files actually present:

```bash
.venv/bin/python scripts/run_silver_reference_jobs.py --job-name DEMO_REFERENCE_JOB --database demo_database --batch-path s3://aws-lakehouse-platform-example/landing/batch_id=tariff-correction --flows tariff_grid --dry-run
```

An index-only incremental batch has no reference files and must skip this stage. After successful reference processing, invoke the Silver index job with its `--silver-interval-table` set to `glue_catalog.<database>.meter_consumption_interval`, then run [Gold pricing and aggregation](glue-gold-jobs.md). Keep Silver/reference writes paused while the Gold snapshot is rebuilt.

## Validation and qualification boundary

Tests verify arguments against actual generated/upload-planned filenames, all four canonical business keys, Gold table compatibility, partial batches, invalid selections/URIs, sequential waits, failure handling and the offline CLI. Gold orchestration uses the same waiting helper and retains its failure/timeout tests. A mocked-provider Terraform plan checks the reference job limits, required runtime arguments and artifact hash/tags. Cleanup tests reject deletion of untagged reference resources.

This change provisions the existing loader without changing its merge or schema-inference behavior. Within a batch, `received_at` breaks equal-version ties; across batches, only a strictly higher `source_version` updates the target. Supply corrections with a higher version. Actual Spark schema inference (including all-null fields), Iceberg writes, AWS permissions and managed Glue execution still require cloud qualification. Local checks do not claim a successful AWS deployment or run.
