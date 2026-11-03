#!/usr/bin/env python3
"""Measure one fragmented table, compact it, then repeat the identical query."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from lakehouse_platform.compaction_benchmark import (
    CompactionProfile, current_snapshot, invocation, measure_phase, report, validate_checkpoint, validate_manifest,
)
from lakehouse_platform.glue_runner import run_glue_jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("database", "workgroup", "glue-job", "script-uri", "module-uri", "benchmark-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--meters", type=int, default=200)
    parser.add_argument("--micro-batches", type=int, default=16)
    parser.add_argument("--start-date", default="2024-01-01")
    parser.add_argument("--query-date")
    parser.add_argument("--repeats", type=int, choices=range(3, 11), default=3)
    parser.add_argument("--region", default="eu-west-3")
    parser.add_argument("--profile")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--inject-post-rewrite-failure", action="store_true", help="Qualification only: stop the first compaction after its commit")
    args = parser.parse_args()
    profile = CompactionProfile(args.benchmark_id, args.history_days, args.meters, args.start_date, args.micro_batches)
    profile.window(args.query_date)
    invocations = {phase: invocation(args.database, args.glue_job, args.script_uri, args.module_uri, profile, phase)
                   for phase in ("build", "compact")}
    if args.inject_post_rewrite_failure:
        invocations["compact"]["FAIL_AFTER_REWRITE"] = "true"
    if args.output.exists():
        raise FileExistsError("Refusing to overwrite a completed compaction report")
    paths = {phase: Path(str(args.output) + f".{phase}.private.json") for phase in ("build", "compact")}
    if not args.resume and any(path.exists() for path in paths.values()):
        raise FileExistsError("Private phase measurements already exist; inspect and use --resume")
    if args.dry_run:
        print(json.dumps({"invocations": invocations, "rows": profile.rows,
                          "expected_result": profile.expected(args.query_date), "measurement_queries": 2 * args.repeats}, indent=2))
        return
    import boto3
    from botocore.exceptions import ClientError
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3, glue, athena = session.client("s3"), session.client("glue"), session.client("athena")
    context = {"database": args.database, "workgroup": args.workgroup, "region": args.region}
    def read(phase):
        uri = urlsplit(invocations[phase]["MANIFEST_URI"])
        try:
            return json.loads(s3.get_object(Bucket=uri.netloc, Key=uri.path.lstrip("/"))["Body"].read())
        except ClientError as error:
            if error.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise
    def checkpoint(phase, manifest):
        validate_manifest(manifest, profile, phase)
        if paths[phase].exists():
            saved = json.loads(paths[phase].read_text())
            if saved["context"] != context:
                raise ValueError("Resume requires the same database, workgroup and region")
            value = saved["measurements"]
            validate_checkpoint(value, profile, manifest, args.repeats, args.query_date)
            return value
        value = measure_phase(athena, args.database, args.workgroup, profile, manifest,
                              repeats=args.repeats, query_date=args.query_date)
        paths[phase].parent.mkdir(parents=True, exist_ok=True)
        with paths[phase].open("x") as handle:
            json.dump({"context": context, "measurements": value}, handle, indent=2)
        return value
    before = read("build") if args.resume else None
    if not before:
        run_glue_jobs(glue, [(args.glue_job, invocations["build"])])
        before = read("build")
    baseline = checkpoint("build", before)
    after = read("compact")
    if not after:
        run_glue_jobs(glue, [(args.glue_job, invocations["compact"])])
        after = read("compact")
    measured = checkpoint("compact", after)
    if current_snapshot(athena, args.database, args.workgroup, profile) != after["source_snapshot"]:
        raise ValueError("Completed compaction snapshot changed before final publication")
    result = report(profile, before, after, baseline, measured)
    result["region"] = args.region
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(f"Validated compaction report: {args.output}")


if __name__ == "__main__":
    main()
