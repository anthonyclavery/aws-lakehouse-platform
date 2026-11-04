# Run the smart-meter demonstration

One entry point, `scripts/run_demo.py`, prepares the sources, previews the business story locally, plans and deploys the ephemeral AWS data plane, runs the governed scenarios, queries Gold and requests guarded cleanup. Run commands from the repository root in Linux/WSL with Python 3.11+.

The fixture has three customers/sites, one for each contract. Nine initial indexes become twelve after a late reading; a higher-version correction changes the tariff allocation. Two genuine processing replays must preserve corrected contents. The history crosses Friday/Saturday and the 22:00/06:00 boundaries in Europe/Paris. This compact demonstration is separate from full 200-site acceptance and benchmark profiles.

## Complete offline preview

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable .
python scripts/run_demo.py --workspace artifacts/demo prepare --run-id demo-preview
python scripts/run_demo.py --workspace artifacts/demo local
python scripts/run_demo.py --workspace artifacts/demo status
```

Open `artifacts/demo/local-report.md`. It shows energy/cost totals and per-contract comparisons for initial, late, corrected, unchanged-replay and older-replay states. JSON retains exact decimal values and fingerprints. The [generated local example](examples/demo-local-report.md) shows exactly 30,000 raw Wh throughout, with energy cost changing from 4.70 to approximately 4.771429 and 4.871429 EUR. Costs cover energy only.

Local mode calls no AWS service and needs no AWS dependency or credentials. Its report explicitly labels expected local results; it does not establish deployed jobs, access isolation, Iceberg metadata stability or cleanup. Three SQL examples appear under `artifacts/demo/queries/` using a deliberately non-deployed example database.

The workspace must be fresh and should stay under ignored `artifacts/`. It contains immutable inputs, their fingerprint and a journal; private AWS outputs/plans/logs later share this directory. `prepare` refuses an existing directory. Each local/AWS scenario journey runs once per workspace. Local preview may precede AWS deployment in the same workspace.

## AWS prerequisites

Use a clean, pushed checkout containing this command and the current cleanup guard. Install the optional dependency in the virtual environment with `python -m pip install --editable '.[aws]'`. Terraform and the persistent [state bootstrap](../terraform/bootstrap-state/README.md), [deployment identity](../terraform/README.md), [ingestion prerequisites](ingestion-access.md) and [cleanup control plane](automated-cleanup.md) must already be configured. The CLI manages the ephemeral demonstration after this one-time setup.

The project profile must assume `aws-lakehouse-platform-demo-terraform-operator`. The same-account governance profile manages the already-supported ingestion/analyst configuration and starts/reads `aws-lakehouse-platform-demo-cleanup`. This workflow adds no permissions to the operator. Credentials remain in memory/existing configuration.

Initialize the existing backend once using its private reviewed configuration:

```bash
terraform -chdir=terraform init -input=false -backend-config=backend.hcl
```

The CLI checks the exact account/region state bucket and `control-plane/terraform.tfstate` key used by native cleanup. Supply the operator's existing reviewed **JSON** variable file, for example `artifacts/operator/demo.tfvars.json`, containing `demo_id`, `expires_at`, `aws_region` and existing identity settings. These values must match the persistent bootstrap/context. A new source `--run-id` is independent of Terraform's namespace. Creating persistent identities or changing their tags requires separately reviewed bootstrap work; the demo plan rejects those changes.

Reserve at least 90 minutes before the existing daily cleanup window; Glue queuing can extend this estimate. Inspect the enabled schedule before starting. This CLI does not move or disable it. Serialize reference/Silver writers and LF account-settings administration during the demonstration.

## Review and apply the saved plan

Define a shell function using your configured profile names:

```bash
demo() {
  python scripts/run_demo.py --workspace artifacts/demo \
    --project-profile lakehouse-project \
    --governance-profile lakehouse-governance --region eu-west-3 "$@"
}
demo plan --var-file artifacts/operator/demo.tfvars.json
```

Review the displayed actions and private `aws/plan.log`, `aws/plan.json` and `aws/plan-summary.json`. Only creates/updates of the existing data-plane allowlist and cleanup context are accepted. Ingestion is enabled; analyst access is initially disabled until Gold exists. The CLI binds profiles/region, saved plan bytes and runtime source files to this workspace.

```bash
demo deploy
demo status
```

`deploy` applies that exact saved plan, checks its output namespace/ingestion role and saves outputs privately. Before marking the deployment ready, it performs up to thirteen actual ingestion-credential probes with five-second pauses (up to sixty seconds of explicit waiting) to allow fresh IAM permissions to propagate. Only credential AccessDenied failures are retried; no source upload or managed job has started at this point. A changed plan or runtime source tree is rejected. It does not regenerate an apply from current configuration. An interrupted apply remains marked `deploying` for inspection.

## Run the AWS business story

```bash
demo run
```

The command prints phase starts/completions and a heartbeat every 30 seconds during long subprocesses. Full stdout/stderr and immediate Glue run journals remain private under `aws/`.

| Phase | Processing and verification |
| --- | --- |
| Initial | Dedicated ingestion publishes references/indexes. Four reference jobs, Silver, pricing and aggregation run. The existing helper enables/seals analyst access and restores LF account settings. |
| Late | Three new indexes split existing intervals. Silver and both Gold jobs run under active governance. |
| Correction | Three version-2 readings change their endpoint/successor allocation while conserving raw Wh. |
| Unchanged replay | Original corrected Bronze bytes/ID are reused with a fresh Gold processing ID. |
| Older replay | Original initial bytes/ID and all four references are replayed with another fresh Gold ID. Higher versions remain authoritative. |

All 23 planned managed runs use normal job definitions and the dedicated transformation role. The existing launcher checks actual ingestion credentials and job roles. Each phase checks complete Silver contents, all three Gold products, eleven internal catalog/SQL refusals, direct Bronze/Silver/Gold object refusals and SELECT withdrawal/restoration. The report reads actual fresh Gold rows as the analyst. Both replays compare full contents and all eleven data-table metadata identities. Failure stops the chain and preserves partial evidence; uncertain managed runs are never automatically resubmitted.

Open `artifacts/demo/aws-report.md` and JSON. Mode/origin distinguishes these readings from local expectations. The business layout is the same, with explicit access/replay checks. The report excludes account IDs, bucket/database pointers, query IDs and raw logs. It does not certify service billing or destruction.

## Explore Gold as an analyst

Only the project profile is needed. The CLI assumes the analyst role in memory and enforces its encrypted engine-3 workgroup and scan cap. It accepts named read-only Gold queries, disables result reuse and checks that reuse did not occur.

```bash
python scripts/run_demo.py --workspace artifacts/demo --project-profile lakehouse-project \
  query --name daily-site --start-date 2026-01-09 --end-date 2026-01-11
python scripts/run_demo.py --workspace artifacts/demo --project-profile lakehouse-project \
  query --name daily-customer --start-date 2026-01-09 --end-date 2026-01-11
python scripts/run_demo.py --workspace artifacts/demo --project-profile lakehouse-project \
  query --name monthly-customer --start-date 2026-01-01 --end-date 2026-02-01
```

Start dates are inclusive; end dates are exclusive. Daily predicates use Athena `DATE` literals. Monthly queries require first-of-month boundaries and filter `YYYY-MM` keys. Optional `--customer-id` accepts a canonical synthetic UUID. Each query saves fresh Markdown/JSON with returned rows, scan bytes and the reuse flag. Empty results are explicit. The [packaged SQL](../src/lakehouse_platform/sql/daily-site.sql) is inspectable; the CLI fills its database/filters from validated inputs.

## Clean up and retain the result

```bash
demo cleanup
demo status
```

Cleanup compares the live isolated state's demo ID/expiry to the workspace and refuses active/unknown managed jobs. It starts the existing guarded CodeBuild project at a clean, pushed revision. The established deletion allowlist and bounded LF-revocation recovery remain authoritative. The CLI retains the build response, verifies data-plane absence from state and preservation of the cleanup context, then checks bucket/database/jobs/temporary-role absence through AWS APIs and persistent identities/versioned state. Original LF account settings must still match the pre-run journal. The daily schedule/control plane are retained.

`cleanup-receipt.json` is separate from the business report. If an interrupted deployment never produced validated outputs, the receipt explicitly marks API/persistent-identity checks as unmeasured; inspect that partial deployment independently. Build success alone does not make those checks true.

After polling times out with a recorded build ID, `demo cleanup` polls that **same build**. An unknown submission outcome without an ID refuses another request; inspect CodeBuild first. A failed native build requires inspection rather than blind resubmission. If daily cleanup already removed resources, absent jobs are accepted and the guarded build may validate the empty remaining scope.

Keep evidence private until reviewed. Public material may contain only reviewed synthetic values; exclude credentials, account/resource pointers, Terraform state/plans, profiles and raw logs. Billing and final independent AWS acceptance remain closing qualification work.

## Failure and progress inspection

`demo status` reads the local journal; it does not poll AWS. Failed scenarios keep completed phases in an explicitly incomplete report. Inspect `failure-*.log`, per-stage stdout/stderr and `aws/<phase>/jobs.jsonl`. A client timeout/interruption can leave a server-side job active. Check its actual state before cleanup or another launch. Preserve failed workspaces and use a fresh one for another complete journey.

An `.operation.lock` prevents overlapping commands. After a crash, remove it only after checking its PID has exited and any AWS work is understood. Runtime-source changes require a fresh plan/workspace. Follow the existing [reference-change/full-refresh contract](late-correction-replay.md) if bounded Gold rejects genuinely changed references; do not edit checkpoints.

Regenerate readable reports without AWS:

```bash
python scripts/run_demo.py --workspace artifacts/demo report --mode local
python scripts/run_demo.py --workspace artifacts/demo report --mode aws
```

## Validation boundary

Tests execute the complete offline CLI and report round-trip, reject source/plan tampering, exercise failure/lock behavior, and drive the AWS adapter with fake service/launcher responses to verify ordering, source IDs, fresh Gold IDs, governance, replay checks and cleanup submission behavior. SQL tests cover Gold-only tables, typed date/month bounds, customer validation and actual-role/workgroup requirements. SQL resources are included in the package build.

The [final AWS qualification](final-acceptance.md) executed this combined entry point through all five phases, all three named analyst queries, report generation and guarded cleanup. It also separately qualified the complete historical 200-site domain, controlled invalid records/quarantine and a contradictory batch rejection. The [actual readable AWS report](examples/demo-aws-report.md), reviewed evidence and [cost model/billing limits](cost-and-destruction.md) are published. This closes the on-demand V1 demonstration scope; the stated production/schema/concurrency limits remain.
