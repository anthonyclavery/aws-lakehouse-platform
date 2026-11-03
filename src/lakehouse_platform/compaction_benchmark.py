"""Same-table Iceberg compaction experiment with snapshot-bound phase evidence."""
from dataclasses import asdict, dataclass
import re

try:  # Both normal package imports and Glue --extra-py-files.
    from .partition_benchmark import (Profile, INTERVAL_COLUMNS, WRITER_PROPERTIES, build_invocation as pruning_invocation,
                                     make_intervals, table_details, validate_database, query_sql, summarize, estimated_scan_cost)
except ImportError:
    from partition_benchmark import (Profile, INTERVAL_COLUMNS, WRITER_PROPERTIES, build_invocation as pruning_invocation,
                                    make_intervals, table_details, validate_database, query_sql, summarize, estimated_scan_cost)

OPTIONS = {"target-file-size-bytes": "134217728", "min-input-files": "2", "rewrite-all": "true",
           "partial-progress.enabled": "false", "max-concurrent-file-group-rewrites": "2"}


@dataclass(frozen=True)
class CompactionProfile(Profile):
    history_days: int = 30
    micro_batches: int = 16

    def __post_init__(self):
        super().__post_init__()
        if (self.history_days > 90 or self.meters > 500 or self.rows > 1_000_000 or
                type(self.micro_batches) is not int or not 2 <= self.micro_batches <= min(32, self.meters)):
            raise ValueError("Compaction envelope: <=90 days, <=500 meters, <=1M rows and 2-32 nonempty micro-batches")

    @property
    def tables(self):
        return {"compaction": f"benchmark_{self.benchmark_id}_compaction"}


def invocation(database, job, script_uri, module_uri, profile, phase):
    if phase not in ("build", "compact"):
        raise ValueError("Expected build or compact phase")
    result = pruning_invocation(database, job, script_uri.replace("/benchmark_compaction.py", "/benchmark_partition_pruning.py"),
        module_uri.replace("/compaction_benchmark.py", "/partition_benchmark.py"), profile)
    # Exact original names must match too; an already-valid pruning script is not a compactor.
    if not script_uri.endswith("/jobs/benchmark_compaction.py") or not module_uri.endswith("/jobs/compaction_benchmark.py"):
        raise ValueError("Use the exact compaction script and module paths")
    bucket = module_uri.split("/")[2]
    prefix = f"s3://{bucket}/landing/batch_id=compaction-{profile.benchmark_id}"
    result.update(scriptLocation=script_uri, **{"extra-py-files": module_uri + "," + module_uri.replace("/compaction_benchmark.py", "/partition_benchmark.py")},
                  PHASE=phase, MICRO_BATCHES=str(profile.micro_batches),
                  MANIFEST_URI=f"{prefix}/{phase}-manifest.json", BUILD_MANIFEST_URI=f"{prefix}/build-manifest.json")
    return result


def validate_manifest(manifest, profile, phase):
    if (manifest.get("profile") != asdict(profile) or manifest.get("phase") != phase or
            manifest.get("rows") != profile.rows or manifest.get("multiset_equal") is not True or
            not re.fullmatch(r"[0-9]+", str(manifest.get("source_snapshot", "")))):
        raise ValueError("Invalid compaction profile, phase, snapshot or full-content proof")
    layout = manifest["layout"]
    if (layout["records"] != profile.rows or layout["data_files"] < 1 or layout["properties"] != WRITER_PROPERTIES or
            layout["partition_spec"] != [{"source_column": "interval_end", "transform": "day"}]):
        raise ValueError("Unexpected compaction table layout or writer properties")
    if phase == "build" and layout["data_files"] <= profile.history_days:
        raise ValueError("The baseline is not fragmented")
    if phase == "compact" and (not re.fullmatch(r"[0-9]+", str(manifest.get("before_snapshot", ""))) or
                                manifest["source_snapshot"] == manifest["before_snapshot"]):
        raise ValueError("Compaction must produce a distinct validated snapshot")


def public_manifest(manifest):
    return {key: value for key, value in manifest.items() if key not in ("source_snapshot", "before_snapshot")}


def build_fragmented(spark, database, profile):
    from pyspark.sql import functions as F
    validate_database(database)
    table = f"glue_catalog.{database}.{profile.tables['compaction']}"
    if spark.catalog.tableExists(table):
        raise ValueError("Compaction table exists; use a fresh benchmark_id")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.shuffle.partitions", "4")
    rows = make_intervals(spark, profile)
    for batch in range(profile.micro_batches):
        fragment = (rows.filter(F.pmod("id", F.lit(profile.micro_batches)) == batch)
                    .repartition(4, F.to_date("interval_end")).sortWithinPartitions("interval_end", "meter_id")
                    .select(*INTERVAL_COLUMNS))
        writer = fragment.writeTo(table)
        if batch == 0:
            writer = writer.using("iceberg").partitionedBy(F.days("interval_end"))
            for key, value in WRITER_PROPERTIES.items():
                writer = writer.tableProperty(key, value)
            writer.create()
        else:
            writer.append()
    expected, actual = rows.select(*INTERVAL_COLUMNS), spark.table(table)
    if expected.exceptAll(actual).limit(1).count() or actual.exceptAll(expected).limit(1).count():
        raise ValueError("Fragmented content differs from the deterministic fixture")
    layout, snapshot = table_details(spark, table)
    result = {"profile": asdict(profile), "phase": "build", "rows": profile.rows, "layout": layout,
              "source_snapshot": snapshot, "multiset_equal": True}
    validate_manifest(result, profile, "build")
    return result


def rewrite_statement(database, profile):
    validate_database(database)
    pairs = ", ".join(f"'{key}', '{value}'" for key, value in OPTIONS.items())
    return (f"CALL glue_catalog.system.rewrite_data_files(table => '{database}.{profile.tables['compaction']}', "
            f"strategy => 'binpack', options => map({pairs}))")


def recovery_action(current, parent, operation, before):
    if current == before:
        return "rewrite"
    if parent == before and operation == "replace":
        return "verify_existing_commit"
    raise ValueError("Unexpected table history; refusing to compact or infer a recovery")


def compact(spark, database, profile, before, *, fail_after_rewrite=False):
    validate_manifest(before, profile, "build")
    validate_database(database)
    table = f"glue_catalog.{database}.{profile.tables['compaction']}"
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    iceberg = spark._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(spark._jsparkSession, table)
    current = iceberg.currentSnapshot()
    action = recovery_action(str(current.snapshotId()), str(current.parentId()), str(current.operation()), before["source_snapshot"])
    if action == "rewrite":
        spark.sql(rewrite_statement(database, profile)).collect()
        if fail_after_rewrite:
            raise RuntimeError("Injected interruption after the compaction commit")
    spark.catalog.refreshTable(table)
    original = spark.read.format("iceberg").option("snapshot-id", before["source_snapshot"]).load(table)
    after = spark.table(table)
    if (original.schema != after.schema or original.exceptAll(after).limit(1).count() or
            after.exceptAll(original).limit(1).count()):
        raise ValueError("Compaction changed the full multiset or schema")
    layout, snapshot = table_details(spark, table)
    if (layout["partition_spec"] != before["layout"]["partition_spec"] or
            layout["properties"] != before["layout"]["properties"] or
            layout["data_files"] >= before["layout"]["data_files"]):
        raise ValueError("Compaction changed configuration or did not reduce active data files")
    result = {"profile": asdict(profile), "phase": "compact", "rows": profile.rows, "layout": layout,
              "source_snapshot": snapshot, "before_snapshot": before["source_snapshot"], "multiset_equal": True,
              "rewrite": {"strategy": "binpack", "options": OPTIONS, "recovered_existing_commit": action != "rewrite"}}
    validate_manifest(result, profile, "compact")
    return result


def current_snapshot(athena, database, workgroup, profile):
    from .qualification import query_rows
    validate_database(database)
    rows, _ = query_rows(athena, database, workgroup,
        f'SELECT CAST(snapshot_id AS varchar) AS id FROM "{database}"."{profile.tables["compaction"]}$history" ORDER BY made_current_at DESC LIMIT 1')
    if len(rows) != 1 or not re.fullmatch(r"[0-9]+", str(rows[0].get("id", ""))):
        raise ValueError("Missing current compaction snapshot")
    return rows[0]["id"]


def measure_phase(athena, database, workgroup, profile, manifest, *, repeats=3, query_date=None):
    from .qualification import query_rows
    validate_manifest(manifest, profile, manifest["phase"])
    if type(repeats) is not int or not 3 <= repeats <= 10:
        raise ValueError("Use 3-10 measurements per phase")
    expected = profile.expected(query_date)
    work = athena.get_work_group(WorkGroup=workgroup)["WorkGroup"]["Configuration"]
    if (not work.get("EnforceWorkGroupConfiguration") or
            work.get("EngineVersion", {}).get("SelectedEngineVersion") != "Athena engine version 3" or
            not 10_000_000 <= work.get("BytesScannedCutoffPerQuery", 0) <= 1073741824 or
            work.get("ResultConfiguration", {}).get("EncryptionConfiguration", {}).get("EncryptionOption") != "SSE_S3"):
        raise ValueError("Require enforced engine 3, SSE-S3 results and <=1 GiB scan cap")
    if current_snapshot(athena, database, workgroup, profile) != manifest["source_snapshot"]:
        raise ValueError("Table changed since the completed phase manifest")
    sql = query_sql(database, profile.tables["compaction"], profile, query_date)
    samples = []
    for _ in range(repeats):
        rows, stats = query_rows(athena, database, workgroup, sql)
        if rows != [expected] or stats.get("ResultReuseInformation", {}).get("ReusedPreviousResult") is not False:
            raise ValueError("Wrong oracle result or reused query")
        fields = {"bytes_scanned": "DataScannedInBytes", "engine_ms": "EngineExecutionTimeInMillis",
                  "planning_ms": "QueryPlanningTimeInMillis", "queue_ms": "QueryQueueTimeInMillis", "total_ms": "TotalExecutionTimeInMillis"}
        if any(type(stats.get(key)) is not int or stats[key] < 0 for key in fields.values()):
            raise ValueError("Missing or invalid Athena measurements")
        sample = {field: stats[key] for field, key in fields.items()}
        sample.update(result_reused=False, scan_cost_model=estimated_scan_cost(sample["bytes_scanned"]))
        samples.append(sample)
    if current_snapshot(athena, database, workgroup, profile) != manifest["source_snapshot"]:
        raise ValueError("Snapshot changed during phase measurement")
    return {"profile": asdict(profile), "phase": manifest["phase"], "source_snapshot": manifest["source_snapshot"],
            "query_date": str(profile.window(query_date)[0]), "repetitions": repeats, "expected_result": expected,
            "samples": samples, "summary": summarize(samples), "results_equal_oracle": True, "snapshot_unchanged": True,
            "workgroup": {"engine": "Athena engine version 3", "enforced": True, "result_encryption": "SSE_S3",
                          "scan_cap_bytes": work["BytesScannedCutoffPerQuery"], "result_reuse": False}}


def validate_checkpoint(checkpoint, profile, manifest, repeats, query_date):
    if (checkpoint.get("profile") != asdict(profile) or checkpoint.get("phase") != manifest["phase"] or
            checkpoint.get("source_snapshot") != manifest["source_snapshot"] or checkpoint.get("repetitions") != repeats or
            checkpoint.get("query_date") != str(profile.window(query_date)[0]) or
            checkpoint.get("expected_result") != profile.expected(query_date) or
            checkpoint.get("results_equal_oracle") is not True or checkpoint.get("snapshot_unchanged") is not True):
        raise ValueError("Saved measurements do not match the requested experiment")
    samples = checkpoint.get("samples", [])
    fields = ("bytes_scanned", "engine_ms", "planning_ms", "queue_ms", "total_ms")
    if (len(samples) != repeats or any(sample.get("result_reused") is not False or
            any(type(sample.get(key)) is not int or sample[key] < 0 for key in fields) or
            sample.get("scan_cost_model") != estimated_scan_cost(sample["bytes_scanned"]) for sample in samples) or
            checkpoint.get("summary") != summarize(samples)):
        raise ValueError("Saved measurement samples are incomplete or inconsistent")


def report(profile, before, after, baseline, measured):
    validate_manifest(before, profile, "build")
    validate_manifest(after, profile, "compact")
    if after["before_snapshot"] != before["source_snapshot"]:
        raise ValueError("Compaction manifest refers to another baseline")
    if (after["layout"]["data_files"] >= before["layout"]["data_files"] or
            after.get("rewrite", {}).get("strategy") != "binpack" or after["rewrite"].get("options") != OPTIONS):
        raise ValueError("Missing file reduction or unexpected rewrite configuration")
    for manifest, checkpoint in ((before, baseline), (after, measured)):
        validate_checkpoint(checkpoint, profile, manifest, baseline["repetitions"], baseline["query_date"])
    if baseline["workgroup"] != measured["workgroup"]:
        raise ValueError("Workgroup settings changed between phases")
    return {"profile": asdict(profile), "rows": profile.rows,
            "sql_template": query_sql("benchmark_database", profile.tables["compaction"], profile, baseline["query_date"]),
            "before": {"manifest": public_manifest(before), "measurements": public_manifest(baseline)},
            "after": {"manifest": public_manifest(after), "measurements": public_manifest(measured)},
            "full_multiset_preserved": True, "partition_and_properties_unchanged": True,
            "measurement_order": "all before queries, one compaction commit, all after queries; no warm-up discarded",
            "cost_assumptions": {"usd_per_tb": "5", "mb_bytes": 10**6, "tb_bytes": 10**12,
                                 "minimum_mb_per_query": 10, "rounding_mb": 1, "estimate_not_invoice": True},
            "limits": ["One synthetic profile; sequential phases cannot eliminate cache or time-order bias.",
                       "Compaction changes clustering/compression too; no universal latency or cost benefit is assumed.",
                       "Active-file reduction does not reclaim retained snapshot files; no snapshot expiration or orphan deletion."]}
