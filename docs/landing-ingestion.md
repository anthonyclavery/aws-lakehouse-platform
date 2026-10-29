# Bronze landing ingestion

The platform has no scheduler or Airflow deployment. Demonstration steps are launched explicitly through versioned scripts so their inputs and timing remain reviewable.

`scripts/upload_landing_batch.py` is the Bronze entrypoint. It verifies every file listed in the generated `manifest.json`, including its SHA-256 checksum, before contacting S3. It uploads source files to `landing/batch_id=<batch-id>/` and publishes `manifest.json` last. A visible manifest therefore indicates a complete, validated landing batch.

The script records the project name, batch identifier, and checksum as S3 object metadata. Replaying the same batch is idempotent: an existing object is reused only when its checksum matches. An existing object with a different checksum fails closed and requires a new batch identifier.

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

Install the optional AWS dependency in the local environment, then use the dedicated project profile. The bucket name is read from reviewed Terraform output and is never committed.

```bash
python -m pip install -e '.[aws]'
python scripts/upload_landing_batch.py \
  --batch-dir artifacts/initial \
  --batch-id initial-20261016 \
  --bucket <lakehouse-bucket> \
  --profile lakehouse-deployer \
  --region eu-west-3
```

Only synthetic batch files and their manifest are uploaded. The script does not accept credentials as command-line arguments and does not log credentials or account identifiers.
