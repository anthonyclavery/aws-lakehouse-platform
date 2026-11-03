# Gold pricing and energy products

Gold supports synthetic `BASE`, `WEEKEND` and `PEAK_OFF_PEAK` energy prices. It excludes subscription fees and taxes. The shared calculator in `src/lakehouse_platform/pricing.py` runs locally and on Glue Python workers; there is no separate SQL tariff implementation.

## Allocation contract

Consumption covers `[interval_start, interval_end)`. Split at local midnight, tariff window edges (including minute edges and overnight windows), and reference validity changes. Resolve exactly one meter/site, site/customer, site/contract and tariff at each segment start, using `[valid_from, valid_to)`. A meter relationship closing exactly at the interval end still owns that interval. An ownership or contract change inside an interval applies only to the remaining segments.

All durations use elapsed UTC time. Tariff windows and dates use `Europe/Paris`. Missing spring hours receive no energy; repeated autumn hours receive their actual elapsed allocation. Both folds of an ambiguous window edge are included. An interval ending at midnight contributes to the preceding consuming day. Energy is allocated uniformly by elapsed time with `allocation_method = PRO_RATA_TIME`; this is an explicit estimation assumption between readings.

Only `VALID` and `VALID_WITH_WARNING` intervals are priced. Negative/non-finite energy, zero/negative duration, missing or overlapping relationships, unsupported contracts, and missing or overlapping tariffs reject the pricing run. No partial interval is emitted. A pricing failure prevents aggregation. Equal window endpoints are rejected; two null endpoints represent a full day. Prices must be finite and non-negative.

The local calculator uses Decimal arithmetic and assigns allocation division residue to the last segment. Glue persists rates, kWh and euros as `DECIMAL(24,12)`, rounded half-even per segment; aggregates use `DECIMAL(30,12)`. Comparison with unrounded local results must allow up to 0.5e-12 per persisted segment and measure, plus Decimal arithmetic residue. These are analytical energy costs, not rounded invoice amounts.

## Jobs and tables

`gold_price_intervals.py` reads the Silver interval and four reference tables. It streams selected endpoints by Spark partition and broadcasts at most 10,000 rows per reference flow. Exceeding this demo bound fails before processing. The source data must be a complete, deduplicated Silver snapshot. The job validates and durably stages all selected priced data before merging `priced_intervals` through Iceberg. Interval/segment boundaries are UTC timestamps, `local_date` is a date, and rows retain meter, site, customer, contract, price, quality and allocation method. The segment key is `(meter_id, interval_end, segment_start, segment_end)`. See [bounded Gold](bounded-gold.md) for the work-ledger checkpoint and retry protocol.

`gold_aggregate_energy.py` reads that snapshot and refreshes affected groups in the following products:

| Table | Grouping | Partition |
| --- | --- | --- |
| `daily_site_energy` | customer, site, local date | local date |
| `daily_customer_energy` | customer, local date | local date |
| `monthly_customer_energy` | customer, local month (`YYYY-MM`) | local month |

Products contain `consumption_kwh` and `energy_cost_eur`. Old/new staged footprints remove obsolete segments and customer/date groups after late data and corrections. Reference snapshot changes require explicit `--refresh-mode full`, covering ownership transfers or changed validity/tariffs. A first run bootstraps the destination; migration from an existing checkpoint-free Gold table requires a fresh destination. It does not repair obsolete rows already present in Silver: those must be corrected upstream first.

Commits are atomic per table, not across all products. Run one Gold chain at a time, with Silver/reference writers paused, and expose results after the chain succeeds. Each Glue job allows one concurrent run. If a commit succeeds before the driver fails, retry the same `BATCH_ID` and `REFRESH_MODE`; staged footprints converge all products before advancing the consumed-work checkpoint. A client wait timeout leaves the AWS run active: inspect its state before retrying. There is no automatic schedule or automatic retry.

## Local execution

Run from the repository root with the package installed in the existing environment:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/generate_initial_data.py --customers 5 --sites 10 --output-dir artifacts/gold-demo/initial
.venv/bin/python scripts/generate_incremental_data.py --initial-batch-dir artifacts/gold-demo/initial --until 2026-10-17T00:00:00Z --output-dir artifacts/gold-demo/incremental
.venv/bin/python scripts/generate_late_or_corrected_batch.py --source-batch-dir artifacts/gold-demo/initial --kind correction --output-dir artifacts/gold-demo/correction
.venv/bin/python scripts/run_local_gold.py artifacts/gold-demo/initial artifacts/gold-demo/incremental artifacts/gold-demo/correction --output-dir artifacts/gold-demo/output
```

Supply the full initial/reference history and every applicable incremental/correction batch. JSONL outputs encode decimals as strings to preserve precision. Replaying the same batches produces the same products. Each output file is replaced atomically after business validation; the four local files do not form one filesystem transaction.

## Terraform and explicit AWS execution

`terraform/glue_gold.tf` defines two Glue 5.0 jobs, five S3 script/module objects updated by content hash and the `gold_job_names` output. Gold reuses the existing Silver transformation role, project bucket and catalog database. Both jobs inherit the existing Iceberg Spark configuration and receive shared Python modules through `--extra-py-files`. They use two G.1X workers, a 30-minute timeout, zero retries and disabled bookmarks; explicit snapshot checkpoints control refreshes.

The project operator policy is extended only for the named Gold jobs and artifacts. Guarded cleanup includes their exact Terraform addresses and requires demo tags, including for the uploaded Gold objects. The cleanup role's job deletion statement belongs to its permission policy, not its trust policy. Apply required operator/bootstrap permission updates before provisioning or relying on scheduled Gold cleanup. The cleanup source must include the new refresh module allowlist entry.

After deployment, load all four Silver reference histories with the [provisioned reference job and sequential runner](glue-reference-job.md), then process the Silver indexes. Use the existing AWS profile configured to assume the project operator role, with the optional AWS Python dependency installed. Obtain job names and database from Terraform outputs, then run:

```bash
.venv/bin/python scripts/run_gold_jobs.py --pricing-job DEMO_GOLD_PRICE_JOB --aggregate-job DEMO_GOLD_AGGREGATE_JOB --database demo_database --batch-id gold-initial --dry-run
.venv/bin/python scripts/run_gold_jobs.py --pricing-job DEMO_GOLD_PRICE_JOB --aggregate-job DEMO_GOLD_AGGREGATE_JOB --database demo_database --batch-id gold-initial --profile PROJECT_OPERATOR_PROFILE --region eu-west-3
```

Replace the uppercase placeholders and `demo_database` with the Terraform output values. The runner waits for pricing success before starting aggregation; terminal failure, expiry, API error or timeout returns a nonzero exit status. Each run ID is printed for inspection. The default wait bound is one hour per job.

The reference table names default to `site_meter_history`, `customer_site_history`, `site_contract_history`, `tariff_grid_history`; the interval input is `meter_consumption_interval`. Match those names when invoking Silver, or override Gold job arguments explicitly. Analysts can query products through Athena after successful completion; Lake Formation analyst grants remain outside this change.

## Validation evidence and limits

The local suite covers all three contracts, weekday/weekend transitions, exact/minute/overnight window edges, midnight/month changes, 23/25-hour DST days and ambiguous/nonexistent window boundaries, temporal ownership/contract/tariff changes, quality filtering, rejected input, energy conservation and deterministic replay. An end-to-end CLI test rebuilds generated references across all contract types, applies index and ownership corrections, checks all three products, and clears an obsolete snapshot. The Glue worker adapter is exercised directly; AWS orchestration and Iceberg write calls are tested with substitutes, including failure and empty snapshots.

Local Terraform formatting and validation cover both roots. `terraform -chdir=terraform test` runs mocked-provider plans and checks job bounds, artifact tags and connected table arguments without contacting AWS. The local environment has no JVM/PySpark runtime; later [managed Glue/Iceberg and Athena qualification](aws-qualification.md) verified the deployed pipeline on controlled synthetic batches. Local checks alone do not establish AWS success; the linked report records the separate live evidence and its limits.

Official runtime references: [AWS Glue Python libraries](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-python-libraries.html) and [AWS Glue Iceberg configuration](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-format-iceberg.html).
