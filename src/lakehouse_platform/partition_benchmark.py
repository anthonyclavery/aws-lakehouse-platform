"""Deterministic scale profile and fail-closed Athena partition benchmark."""

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
import re
from statistics import median
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Profile:
    benchmark_id: str
    history_days: int = 730
    meters: int = 200
    start_date: str = "2024-01-01"

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,24}", self.benchmark_id):
            raise ValueError("benchmark_id requires 3-25 lowercase letters, digits or underscores")
        if (type(self.history_days) is not int or not 2 <= self.history_days <= 1096 or
                type(self.meters) is not int or not 1 <= self.meters <= 1000 or self.rows > 20_000_000):
            raise ValueError("Profile exceeds the bounded days/meters/20-million-row envelope")
        if date.fromisoformat(self.start_date).isoformat() != self.start_date:
            raise ValueError("start_date must be an ISO date")
        date.fromisoformat(self.start_date) + timedelta(days=self.history_days)

    @property
    def rows(self):
        return self.history_days * 24 * self.meters

    @property
    def tables(self):
        return {layout: f"benchmark_{self.benchmark_id}_{layout}" for layout in ("unpartitioned", "daily")}

    def window(self, query_date=None):
        start = date.fromisoformat(self.start_date)
        day = start + timedelta(days=self.history_days // 2) if query_date is None else date.fromisoformat(query_date)
        if not start <= day < start + timedelta(days=self.history_days):
            raise ValueError("Query day must be inside the generated history")
        return day, day + timedelta(days=1)

    def expected(self, query_date=None):
        day, _ = self.window(query_date)
        first_hour = (day - date.fromisoformat(self.start_date)).days * 24
        total = sum(100 + ((hour * self.meters + meter) * 37 + hour * 13) % 4901
                    for hour in range(first_hour, first_hour + 24) for meter in range(self.meters))
        return {"rows": str(24 * self.meters), "meters": str(self.meters), "consumption_wh": str(total),
                "first_reading": f"{day} 00:00:00", "last_reading": f"{day} 23:00:00"}


def validate_database(database):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,254}", database):
        raise ValueError("Simple lowercase Glue database identifier required")


def query_sql(database, table, profile, query_date=None):
    validate_database(database)
    if table not in profile.tables.values():
        raise ValueError("Query table is outside this benchmark")
    day, end = profile.window(query_date)
    return f"""SELECT COUNT(*) AS rows, COUNT(DISTINCT meter_id) AS meters,
SUM(consumption_wh) AS consumption_wh,
date_format(MIN(interval_end), '%Y-%m-%d %H:%i:%s') AS first_reading,
date_format(MAX(interval_end), '%Y-%m-%d %H:%i:%s') AS last_reading
FROM "{database}"."{table}"
WHERE interval_end >= TIMESTAMP '{day} 00:00:00'
  AND interval_end < TIMESTAMP '{end} 00:00:00'"""


def estimated_scan_cost(byte_count, usd_per_tb=Decimal("5")):
    if type(byte_count) is not int or byte_count < 0 or not usd_per_tb.is_finite() or usd_per_tb < 0:
        raise ValueError("Finite nonnegative scan bytes/rate required")
    billed = max(10_000_000, ((byte_count + 999_999) // 1_000_000) * 1_000_000)
    return {"rounded_bytes": billed, "estimated_usd": str(Decimal(billed) * usd_per_tb / Decimal(10**12))}


def summarize(samples):
    if len(samples) < 3:
        raise ValueError("At least three measurements per layout required")
    fields = ("bytes_scanned", "engine_ms", "planning_ms", "queue_ms", "total_ms")
    return {f"median_{field}": median(s[field] for s in samples) for field in fields}


def build_invocation(database, job, script_uri, module_uri, profile):
    validate_database(database)
    if (not database.startswith("aws_lakehouse_platform_") or
            not re.fullmatch(r"aws-lakehouse-platform-[a-z0-9-]{3,32}-gold-price", job)):
        raise ValueError("Use the project database and existing Gold pricing runtime")
    script, module = urlsplit(script_uri), urlsplit(module_uri)
    for uri, filename in ((script, "benchmark_partition_pruning.py"), (module, "partition_benchmark.py")):
        if (uri.scheme != "s3" or not re.fullmatch(r"aws-lakehouse-platform-[a-z0-9-]{3,40}", uri.netloc) or
                uri.path != f"/jobs/{filename}" or uri.query or uri.fragment):
            raise ValueError("Benchmark artifacts require exact project bucket/job paths")
    if script.netloc != module.netloc:
        raise ValueError("Benchmark artifacts must share the deployment bucket")
    manifest = f"s3://{script.netloc}/landing/batch_id=benchmark-{profile.benchmark_id}/benchmark-manifest.json"
    return {"scriptLocation": script_uri, "extra-py-files": module_uri, "DATABASE": database,
            "BENCHMARK_ID": profile.benchmark_id, "HISTORY_DAYS": str(profile.history_days),
            "METERS": str(profile.meters), "START_DATE": profile.start_date, "MANIFEST_URI": manifest}


def validate_manifest(manifest, profile):
    if manifest.get("profile") != asdict(profile) or manifest.get("rows") != profile.rows or not manifest.get("multiset_equal"):
        raise ValueError("Build manifest does not prove the requested identical dataset")
    if (set(manifest.get("source_snapshots", {})) != set(profile.tables) or
            any(not re.fullmatch(r"[0-9]+", str(value)) for value in manifest["source_snapshots"].values())):
        raise ValueError("Build manifest must bind both tables to their built snapshots")
    for layout, transforms in (("unpartitioned", []), ("daily", [{"source_column": "interval_end", "transform": "day"}])):
        result = manifest["layouts"][layout]
        if (result["partition_spec"] != transforms or result["records"] != profile.rows or result["data_files"] < 1 or
                result["properties"] != {"write.format.default": "parquet", "write.parquet.compression-codec": "zstd",
                                         "write.target-file-size-bytes": "134217728", "write.distribution-mode": "none"}):
            raise ValueError("Unexpected table layout, records or writer properties")


def measure(athena, database, workgroup, profile, manifest, *, repeats=3, query_date=None, usd_per_tb=Decimal("5")):
    from .qualification import query_rows
    validate_manifest(manifest, profile)
    validate_database(database)
    estimated_scan_cost(0, usd_per_tb)
    if type(repeats) is not int or not 3 <= repeats <= 10:
        raise ValueError("Use 3-10 interleaved repetitions")
    expected = profile.expected(query_date)
    work = athena.get_work_group(WorkGroup=workgroup)["WorkGroup"]["Configuration"]
    if (not work.get("EnforceWorkGroupConfiguration") or
            work.get("EngineVersion", {}).get("SelectedEngineVersion") != "Athena engine version 3" or
            not 10_000_000 <= work.get("BytesScannedCutoffPerQuery", 0) <= 1073741824 or
            work.get("ResultConfiguration", {}).get("EncryptionConfiguration", {}).get("EncryptionOption") != "SSE_S3"):
        raise ValueError("Benchmark requires enforced engine 3, encrypted results and a <=1 GiB scan cap")
    def snapshots():
        result = {}
        for layout, table in profile.tables.items():
            rows, _ = query_rows(athena, database, workgroup,
                f'SELECT CAST(snapshot_id AS varchar) AS id FROM "{database}"."{table}$history" ORDER BY made_current_at DESC LIMIT 1')
            if len(rows) != 1:
                raise ValueError("Benchmark table has no current snapshot")
            result[layout] = rows[0]["id"]
        return result
    before = snapshots()
    if before != manifest["source_snapshots"]:
        raise ValueError("Tables changed since the validated build manifest")
    samples = {layout: [] for layout in profile.tables}
    order = []
    for repeat in range(repeats):
        # Alternate the first layout to reduce systematic warm-up/order bias.
        for layout in (list(profile.tables) if repeat % 2 == 0 else list(reversed(profile.tables))):
            rows, stats = query_rows(athena, database, workgroup,
                query_sql(database, profile.tables[layout], profile, query_date))
            if rows != [expected]:
                raise ValueError("Athena result differs from the independent integer profile oracle")
            if stats.get("ResultReuseInformation", {}).get("ReusedPreviousResult") is not False:
                raise ValueError("Result reuse must be explicitly reported false")
            fields = {"bytes_scanned": "DataScannedInBytes", "engine_ms": "EngineExecutionTimeInMillis",
                      "planning_ms": "QueryPlanningTimeInMillis", "queue_ms": "QueryQueueTimeInMillis",
                      "total_ms": "TotalExecutionTimeInMillis"}
            if any(type(stats.get(k)) is not int or stats[k] < 0 for k in fields.values()):
                raise ValueError("Required Athena measurement statistics are missing or invalid")
            sample = {field: stats[key] for field, key in fields.items()}
            sample["scan_cost_model"] = estimated_scan_cost(sample["bytes_scanned"], usd_per_tb)
            sample["result_reused"] = False
            samples[layout].append(sample)
            order.append(layout)
    if before != snapshots():
        raise ValueError("Benchmark snapshots changed during measurement")
    summaries = {layout: summarize(values) for layout, values in samples.items()}
    base = summaries["unpartitioned"]["median_bytes_scanned"]
    public_manifest = {key: value for key, value in manifest.items() if key != "source_snapshots"}
    return {"profile": asdict(profile), "rows": profile.rows, "build": public_manifest, "expected_result": expected,
            "query_day": str(profile.window(query_date)[0]), "repetitions": repeats, "execution_order": order,
            "sql_templates": {layout: query_sql("benchmark_database", table, profile, query_date) for layout, table in profile.tables.items()},
            "workgroup": {"engine": "Athena engine version 3", "scan_cap_bytes": work["BytesScannedCutoffPerQuery"],
                          "enforced": True, "result_encryption": "SSE_S3", "result_reuse": False},
            "samples": samples, "summaries": summaries, "snapshots_unchanged": True, "results_equal_oracle": True,
            "median_scan_reduction_fraction": str(Decimal(base - summaries["daily"]["median_bytes_scanned"]) / Decimal(base)) if base else None,
            "cost_assumptions": {"usd_per_tb": str(usd_per_tb), "mb_bytes": 10**6, "tb_bytes": 10**12,
                                 "minimum_mb_per_query": 10, "rounding_mb": 1,
                                 "excludes": "Glue, S3, catalog, metadata validation queries and taxes; estimate is not an invoice"}}


def build_tables(spark, database, profile):
    """Create two fresh Iceberg tables; never overwrite a pre-existing table."""
    from pyspark.sql import functions as F
    validate_database(database)
    tables = {layout: f"glue_catalog.{database}.{name}" for layout, name in profile.tables.items()}
    if any(spark.catalog.tableExists(table) for table in tables.values()):
        raise ValueError("Benchmark tables already exist; use a fresh benchmark_id")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.shuffle.partitions", "16")
    rows = (spark.range(profile.rows).withColumn("hour", F.floor(F.col("id") / profile.meters).cast("long"))
            .withColumn("meter_id", F.concat(F.lit("meter_"), F.pmod("id", F.lit(profile.meters)).cast("string"))))
    epoch = (date.fromisoformat(profile.start_date) - date(1970, 1, 1)).days * 86400
    rows = (rows.withColumn("interval_end", F.timestamp_seconds(F.lit(epoch) + F.col("hour") * 3600))
            .withColumn("interval_start", F.timestamp_seconds(F.lit(epoch) + (F.col("hour") - 1) * 3600))
            .withColumn("consumption_wh", (100 + F.pmod(F.col("id") * 37 + F.col("hour") * 13, F.lit(4901))).cast("long"))
            .withColumn("quality_status", F.lit("VALID")))
    columns = ("meter_id", "interval_start", "interval_end", "consumption_wh", "quality_status")
    properties = {"write.format.default": "parquet", "write.parquet.compression-codec": "zstd",
                  "write.target-file-size-bytes": "134217728", "write.distribution-mode": "none"}
    for layout, table in tables.items():
        if layout == "unpartitioned":
            # Deliberately scatter history through every file to avoid accidental
            # date clustering substituting for a useful partition specification.
            ordered = rows.repartition(16, F.pmod("id", F.lit(16))).sortWithinPartitions(F.xxhash64("id"))
        else:
            ordered = rows.repartition(16, F.to_date("interval_end")).sortWithinPartitions("interval_end", "meter_id")
        writer = ordered.select(*columns).writeTo(table).using("iceberg")
        for key, value in properties.items():
            writer = writer.tableProperty(key, value)
        if layout == "daily":
            writer = writer.partitionedBy(F.days("interval_end"))
        writer.create()
    base, daily = (spark.table(tables[k]) for k in ("unpartitioned", "daily"))
    if base.exceptAll(daily).limit(1).count() or daily.exceptAll(base).limit(1).count():
        raise ValueError("Persisted benchmark datasets differ")
    layouts, source_snapshots = {}, {}
    for layout, table in tables.items():
        files = spark.table(table + ".files").filter("content = 0")
        summary = files.agg(F.count("*").alias("data_files"), F.sum("record_count").alias("records"),
            F.sum("file_size_in_bytes").alias("data_bytes"), F.min("file_size_in_bytes").alias("min_file_bytes"),
            F.max("file_size_in_bytes").alias("max_file_bytes"),
            F.percentile_approx("file_size_in_bytes", 0.5).alias("median_file_bytes")).collect()[0].asDict()
        iceberg = spark._jvm.org.apache.iceberg.spark.Spark3Util.loadIcebergTable(spark._jsparkSession, table)
        source_snapshots[layout] = str(iceberg.currentSnapshot().snapshotId())
        fields = iceberg.spec().fields()
        summary["partition_spec"] = [{"source_column": iceberg.schema().findColumnName(fields.get(i).sourceId()),
                                      "transform": str(fields.get(i).transform())} for i in range(fields.size())]
        summary["properties"] = {key: str(iceberg.properties().get(key)) for key in properties}
        layouts[layout] = summary
    manifest = {"profile": asdict(profile), "rows": profile.rows, "multiset_equal": True, "layouts": layouts,
                "source_snapshots": source_snapshots,
                "ordering": {"unpartitioned": "16 hash-distributed tasks, xxhash64 row ordering",
                             "daily": "16 day-distributed tasks, interval_end/meter ordering"}}
    validate_manifest(manifest, profile)
    return manifest
