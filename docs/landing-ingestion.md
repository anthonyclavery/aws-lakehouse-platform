# Bronze landing ingestion

The platform has no scheduler or Airflow deployment. Demonstration steps are launched explicitly through versioned scripts so their inputs and timing remain reviewable.

`scripts/upload_landing_batch.py` is the Bronze entrypoint. It verifies every file listed in the generated `manifest.json`, including its SHA-256 checksum, before contacting S3. It uploads source files to `landing/batch_id=<batch-id>/` and publishes `manifest.json` last. A visible manifest therefore indicates a complete, validated landing batch.

Every object, including the final manifest, uses a conditional S3 `PutObject` with `If-None-Match: *`. There is no separate existence check followed by an unconditional upload. Concurrent writes therefore cannot replace an already-present key through this uploader. The request includes the validated file's Base64 SHA-256 as `ChecksumSHA256`, so S3 rejects bytes that changed after local validation.

The script also records the project name, batch identifier, and checksum as object metadata. Replaying the same batch is idempotent: after a native `PreconditionFailed`/HTTP 412, it streams and hashes the stored object's actual bytes. Metadata alone is not proof; missing or forged metadata never permits an overwrite. Matching bytes are reused, and different bytes fail closed with `ImmutableObjectConflict`, requiring a new batch identifier. Read failures, authorization failures, checksum rejection and concurrent-delete HTTP 409 errors propagate and stop publication. Retry the complete batch after resolving such an error; already-matching objects remain reusable.

Immutability here is an uploader guarantee, not S3 Object Lock or a bucket-wide policy against other writers. Use this uploader for source batches and keep cleanup/deletion serialized with ingestion. Each object is atomic, while the batch is not a transaction: an interrupted or conflicting upload can leave source objects without a manifest. Downstream processing must consume only manifest-backed batches. The upload streams one `PutObject` per file rather than multipart transfer; split files exceeding that API's supported object size before ingestion. Full-byte verification on replay incurs S3 reads.

## Local validation

```bash
python scripts/generate_initial_data.py --output-dir artifacts/initial
python scripts/upload_landing_batch.py \
  --batch-dir artifacts/initial \
  --batch-id initial-20261016 \
  --bucket <lakehouse-bucket> \
  --dry-run
```

## Approved demonstration upload

Install the optional AWS dependency in the local environment, then use the dedicated project profile to assume the [Bronze ingestion role](ingestion-access.md). The bucket and role ARN are read from reviewed Terraform output and are never committed.

```bash
python -m pip install -e '.[aws]'
python scripts/upload_landing_batch.py \
  --batch-dir artifacts/initial \
  --batch-id initial-20261016 \
  --bucket <lakehouse-bucket> \
  --profile lakehouse-deployer \
  --region eu-west-3 \
  --role-arn <ingestion-role-arn>
```

Only synthetic batch files and their manifest are uploaded. The script does not accept credentials as command-line arguments and does not log credentials or account identifiers.

## Native concurrency qualification

On an already provisioned disposable demonstration, generate a small synthetic batch and run:

```bash
python scripts/qualify_landing_atomicity.py \
  --batch-dir artifacts/initial \
  --batch-prefix atomic-bronze-unique-run \
  --bucket <lakehouse-bucket> \
  --profile lakehouse-deployer \
  --region eu-west-3 \
  --report artifacts/atomic-landing/report.json
```

The qualifier requires project/demo/automatic-cleanup bucket tags, fresh case prefixes and a fresh report path. It runs two concurrent writers against one batch, verifies complete stored bytes and unchanged replay, seeds conflicting objects with missing or spoofed metadata, and checks native checksum rejection. It leaves only its synthetic probes under `landing/` for the existing guarded cleanup to remove. It publishes no manifest for rejected cases and emits no infrastructure identifiers in its report. This is an S3 upload qualification, not a new Glue business qualification or a separate ingestion role.

The [2026-11-03 evidence](evidence/atomic-landing-2026-11-03.json) records the managed result and post-test cleanup checks. See AWS's [conditional-write behavior](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html) and [PutObject checksum contract](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html).

The managed run created each of five batch objects once across two simultaneous uploads, observed five native HTTP 412 responses, verified all stored bytes and an unchanged replay, preserved conflicting objects with missing/spoofed metadata without publishing their manifests, and observed native `BadDigest`/400 rejection without creating an object. The guarded cleanup succeeded in one pass with 22 deletions. The first independent bucket-absence probe still observed the bucket; repeated verification confirmed it and the rest of the data plane absent, with persistent identities and versioned state preserved. The published checks describe that final verified state. Local validation passed 171 tests, including conflicting concurrent writers and resuming publication after a manifest failure.

## Explicit Silver execution

After the Glue job and Iceberg tables have been provisioned, `scripts/run_silver_index_job.py` starts one named job run for one landed batch. `--wait` makes the script return a failure status when Glue reports a failed terminal state. It is an explicit demonstration command, not a scheduler.
