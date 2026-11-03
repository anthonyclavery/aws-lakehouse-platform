# Integrated AWS identity-chain qualification

This qualification follows source-shaped data through the dedicated ingestion role, the normal reference/Silver/Gold jobs and the analyst role. It does not create Gold directly with a fixture job or override the deployed job scripts. The same initial and append batches are inputs to the local calculator and the managed pipeline.

## Fixture and expected result

`scripts/generate_integrated_fixture.py` creates three synthetic customers/sites/meters, one for each contract, and five tariff rules. The initial nine indexes produce six intervals. They span Friday 21:00 to Saturday 07:00 in Europe/Paris, crossing the weekend boundary and both the 22:00 and 06:00 tariff boundaries. Three later indexes extend each meter by one hour after access governance has been enabled.

| Phase | Indexes | Intervals | Each product's total kWh | Each product's total EUR | Daily site/customer groups | Monthly customer groups |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Initial | 9 | 6 | 30 | 4.70 | 6 / 6 | 3 |
| After append | 12 | 9 | 33 | 5.30 | 6 / 6 | 3 |

The initial per-contract costs are BASE 2.00, WEEKEND 1.30 and PEAK_OFF_PEAK 1.40 EUR. Hand-calculated unit assertions cover those totals and the daily allocations. The full Gold oracle uses the existing shared tariff calculator, not an independent second pricing engine. This small fixture is a correctness/access test, not a scale benchmark, negative-input quarantine qualification or new late/correction/recovery campaign.

## Execution sequence

Provision the current demonstration with ingestion enabled and analyst access initially disabled. Follow the [ingestion](ingestion-access.md) and [analyst](analyst-access.md) policy prerequisites. Keep persistent identities/state and the normal cleanup schedule. Save Terraform outputs privately. Do not change deployment, source data or permissions while a job is still running.

Generate the fixture in a fresh directory:

```bash
python scripts/generate_integrated_fixture.py --output-dir artifacts/integrated/batches
python scripts/run_integrated_batch.py \
  --outputs artifacts/integrated/outputs.json \
  --batch-dir artifacts/integrated/batches/initial \
  --batch-id integrated-initial --load-references \
  --project-profile PROJECT_OPERATOR_PROFILE \
  --artifact-dir artifacts/integrated/initial
```

The runner validates one demo's bucket/database/job names and requires the manifest to contain every planned input. It verifies the actual short-lived ingestion identity and each job's configured transformation role before uploading. The ingestion session publishes all five source files plus the manifest with conditional writes. The project operator launches four sequential reference runs, one Silver index run, Gold pricing and Gold aggregation. All seven use the deployed production job definitions with queuing enabled and no script override.

Every run ID is recorded as soon as Glue returns it, before waiting. A terminal failure or timeout stops the chain and leaves its private journal; no successful report is produced. A timeout does not stop the server-side job: inspect its actual state before retrying. Use a fresh artifact directory for another attempt and preserve the failed attempt's journal. This explicit launcher is not an orchestration service or an automatic resume scheduler. If newly created IAM permissions are not yet effective, assumption fails before ingestion; inspect trust/caller permissions before retrying.

Audit Silver through the operator's read/query boundary:

```bash
python scripts/qualify_integrated_silver.py artifacts/integrated/batches/initial \
  --outputs artifacts/integrated/outputs.json \
  --project-profile PROJECT_OPERATOR_PROFILE \
  --report artifacts/integrated/silver-initial.json
```

This compares complete contracted index fields, interval keys/measures/status and all four reference histories against local processing, with canonical timestamp encodings and fingerprints. It rejects duplicate or missing rows and version drift. It also checks zero quarantine/unfinished work and nonempty completed Silver/Gold work. It requires a valid positive fixture rather than silently accepting empty inputs.

Now enable and seal analyst Gold access using the [existing guarded helper](analyst-access.md). The private variable file must retain `ingestion_access_enabled=true` and its governance profile so enabling the analyst does not remove ingestion. Restore original LF account settings before proceeding. Refresh the private outputs file. Generate the expected Gold arrays from `lakehouse_platform.qualification.expected_products([initial_directory])` and run `scripts/qualify_analyst_access.py` with the initial index object as the existing Bronze probe. Require complete Gold content comparison, actual assumed-role credentials, result reuse disabled, direct S3 refusals, internal catalog/query refusals and LF SELECT revocation/restoration. The internal denial inventory includes `quarantine_records`.

With LF governance active, ingest and process the append through the same dedicated identities:

```bash
python scripts/run_integrated_batch.py \
  --outputs artifacts/integrated/outputs.json \
  --batch-dir artifacts/integrated/batches/append \
  --batch-id integrated-append \
  --project-profile PROJECT_OPERATOR_PROFILE \
  --artifact-dir artifacts/integrated/append
```

This adds three managed runs without reloading references. It is new data, not a completed Gold-run replay: Silver and Gold must actually update after sealing. Audit Silver with both batch directories, regenerate expected Gold from both, and repeat the real analyst qualification. Require the changed complete Gold fingerprints and final totals above. Run the [ingestion access qualifier](ingestion-access.md) with another fresh, unprocessed probe prefix to verify mutation/control-plane refusals on this same environment; do not include that probe prefix as processed history.

Finally require an enabled-feature Terraform plan with zero managed changes, commit/push the qualified source and run the [guarded cleanup](cleanup-recovery.md) at that revision. Verify both ingestion and analyst identities/policies, Gold registrations/grants and the complete data plane absent; verify original LF settings, persistent identities, versioned state, cleanup context and enabled schedule preserved. Review and sanitize evidence before publication. Keep raw job/query logs, profiles, outputs, settings journals, state and plans private.

## Managed result on 2026-11-03

All ten managed runs succeeded: four reference runs, initial Silver/pricing/aggregation, then append Silver/pricing/aggregation after LF governance was active. An independent API audit found exactly those ten unique runs on the fresh job definitions, matched their arguments to the private journal, verified their configured writer role, and compared the seven deployed Python artifacts byte-for-byte to repository sources. There were no script overrides. One reference run waited for native Glue capacity before succeeding without resubmission. A first ingestion-role assumption had failed before upload; read-only trust/policy checks and a later assumption succeeded without policy mutation.

Complete Silver comparisons matched all contracted source/index/interval/reference fields before and after append: nine then twelve indexes, six then nine intervals, unchanged reference histories, empty quarantine and no unfinished work. Each of the three analyst Gold products matched exact local fingerprints with zero measure error in both phases, at the totals in the table above. Group counts stayed 6/6/3 while every product fingerprint changed after append. Actual analyst sessions refused all eleven internal tables, including quarantine, and direct Bronze/Silver/Gold objects. LF SELECT withdrawal and restoration passed in each phase. A fresh ingestion session also passed the nineteen mutation/non-Bronze/control-plane refusal checks on this same governed environment.

The exercise fixed normalization of Athena's `... UTC` timestamp representation in the Silver comparator; it did not change source data or deployed processing logic. Local validation passed 189 Python tests and nine Terraform mock scenarios, with both Terraform roots validated. The enabled-feature convergence plan had no managed changes.

The pushed revision's native cleanup succeeded in two passes: 59 initial deletions, seven LF self-revoke errors, then a revalidated strict subset of eight remaining deletions with no remaining LF revocations. Independent API/state checks confirmed the entire data plane, all three temporary roles/policies, analyst workgroup, Gold registrations and scoped grants absent. Original LF settings were restored after bootstrap and the final read audit; persistent identities, versioned state, cleanup context and the enabled schedule remained. See the [sanitized evidence](evidence/integrated-chain-2026-11-03.json).
