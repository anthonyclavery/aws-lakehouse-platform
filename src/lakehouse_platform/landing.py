"""Plan and perform immutable uploads of synthetic batches to Bronze S3."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


PROJECT_NAME = "aws-lakehouse-platform"
_BATCH_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


class LandingBatchRejected(ValueError):
    """Raised when a local batch is not a safe, manifest-backed landing batch."""


class ImmutableObjectConflict(ValueError):
    """Raised when an existing landing object does not match the local batch."""


class S3Client(Protocol):
    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...

    def upload_file(self, Filename: str, Bucket: str, Key: str, ExtraArgs: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class LandingObject:
    path: Path
    key: str
    sha256: str


@dataclass(frozen=True)
class UploadResult:
    uploaded_keys: tuple[str, ...]
    existing_keys: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_batch_file(batch_dir: Path, relative_path: str) -> Path:
    candidate = (batch_dir / relative_path).resolve()
    try:
        candidate.relative_to(batch_dir.resolve())
    except ValueError as error:
        raise LandingBatchRejected(f"Manifest path escapes the batch directory: {relative_path!r}") from error
    if not candidate.is_file():
        raise LandingBatchRejected(f"Manifest file does not exist: {relative_path!r}")
    return candidate


def plan_landing_upload(batch_dir: Path, batch_id: str) -> tuple[LandingObject, ...]:
    """Return verified batch objects, with the manifest deliberately last."""
    if not _BATCH_ID_PATTERN.fullmatch(batch_id):
        raise LandingBatchRejected("batch_id must contain 3 to 64 lowercase letters, digits, or hyphens.")

    resolved_batch_dir = batch_dir.resolve()
    manifest_path = resolved_batch_dir / "manifest.json"
    if not manifest_path.is_file():
        raise LandingBatchRejected("Batch directory must contain manifest.json.")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LandingBatchRejected("manifest.json is not valid JSON.") from error

    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise LandingBatchRejected("manifest.json must declare at least one file.")

    prefix = f"landing/batch_id={batch_id}"
    planned: list[LandingObject] = []
    seen_paths: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
            raise LandingBatchRejected("Each manifest file entry requires path and sha256 strings.")
        relative_path = entry["path"]
        if relative_path in seen_paths:
            raise LandingBatchRejected(f"Manifest repeats a file path: {relative_path!r}")
        seen_paths.add(relative_path)
        source_path = _safe_batch_file(resolved_batch_dir, relative_path)
        actual_sha256 = _sha256(source_path)
        if actual_sha256 != entry["sha256"]:
            raise LandingBatchRejected(f"Checksum does not match manifest for {relative_path!r}.")
        planned.append(LandingObject(source_path, f"{prefix}/{relative_path}", actual_sha256))

    planned.append(LandingObject(manifest_path, f"{prefix}/manifest.json", _sha256(manifest_path)))
    return tuple(planned)


def _is_not_found(error: Exception) -> bool:
    response = getattr(error, "response", {})
    code = str(response.get("Error", {}).get("Code", "")) if isinstance(response, dict) else ""
    return code in {"404", "NoSuchKey", "NotFound"}


def _existing_sha256(client: S3Client, bucket: str, key: str) -> str | None:
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except Exception as error:
        if _is_not_found(error):
            return None
        raise
    metadata = response.get("Metadata") or {}
    return metadata.get("sha256")


def upload_landing_batch(client: S3Client, bucket: str, batch_dir: Path, batch_id: str) -> UploadResult:
    """Upload a verified batch once; fail if a supposedly immutable object differs."""
    uploaded: list[str] = []
    existing: list[str] = []
    for item in plan_landing_upload(batch_dir, batch_id):
        existing_sha256 = _existing_sha256(client, bucket, item.key)
        if existing_sha256 is not None:
            if existing_sha256 != item.sha256:
                raise ImmutableObjectConflict(f"Landing object already exists with a different checksum: {item.key}")
            existing.append(item.key)
            continue
        client.upload_file(
            Filename=str(item.path),
            Bucket=bucket,
            Key=item.key,
            ExtraArgs={
                "Metadata": {
                    "batch-id": batch_id,
                    "project": PROJECT_NAME,
                    "sha256": item.sha256,
                }
            },
        )
        uploaded.append(item.key)
    return UploadResult(tuple(uploaded), tuple(existing))
