"""Run inside Glue 5 with production --extra-py-files and a disposable database.

Only qualification_gold_* tables are touched. The caller owns provisioning and
guarded teardown. Output is synthetic and contains no deployment identifiers.
"""
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_EVEN
import json
import sys

from awsglue.context import GlueContext
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
import gold_refresh as refresh
from gold_spark import SEGMENT_COLUMNS, price_partition, utc_record
from pricing import gold_products, iter_priced_segments


def main():
    database = getResolvedOptions(sys.argv, ["DATABASE"])["DATABASE"]
    if not __import__("re").fullmatch(r"[a-z][a-z0-9_]*", database):
        raise ValueError("Invalid database")
    spark = GlueContext(SparkContext.getOrCreate()).spark_session
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "true")
    spark.conf.set("spark.sql.shuffle.partitions", "4")
    names = {k: "qualification_gold_verified_" + k.lower().removesuffix("_table") for k in (
        "INTERVAL_TABLE", "SITE_METER_TABLE", "CUSTOMER_SITE_TABLE", "SITE_CONTRACT_TABLE", "TARIFF_TABLE",
        "PRICED_INTERVAL_TABLE", "DAILY_SITE_TABLE", "DAILY_CUSTOMER_TABLE", "MONTHLY_CUSTOMER_TABLE")}
    args = {k: f"glue_catalog.{database}.{v}" for k, v in names.items()}
    args.update(BATCH_ID="gold-initial", REFRESH_MODE="bounded")
    validity = {"valid_from": "2020-01-01T00:00:00Z", "valid_to": None}
    refs = {
        "site_meter": [{**validity, "meter_id": m, "site_id": s} for m, s in (("m", "s"), ("other", "other"))],
        "customer_site": [{**validity, "site_id": s, "customer_id": c} for s, c in (("s", "c"), ("other", "other"))],
        "site_contract": [{**validity, "site_id": s, "contract_type": "BASE"} for s in ("s", "other")],
        "tariff_grid": [{**validity, "contract_type": "BASE", "day_scope": "ALL_DAYS", "time_from_local": None,
                         "time_to_local": None, "price_eur_per_kwh": "0.20"}],
    }
    schemas = {
        "site_meter": "meter_id string, site_id string, valid_from string, valid_to string",
        "customer_site": "site_id string, customer_id string, valid_from string, valid_to string",
        "site_contract": "site_id string, contract_type string, valid_from string, valid_to string",
        "tariff_grid": "contract_type string, day_scope string, time_from_local string, time_to_local string, price_eur_per_kwh string, valid_from string, valid_to string",
    }
    from pyspark.sql import functions as F
    def save_refs():
        for flow, key in refresh.REFERENCES:
            df = spark.createDataFrame(refs[flow], schemas[flow])
            for column in ("valid_from", "valid_to"):
                df = df.withColumn(column, F.to_timestamp(column))
            df.writeTo(args[key]).using("iceberg").createOrReplace()
    save_refs()
    source = [{"meter_id": "other", "interval_start": datetime(2026, 1, 1) + timedelta(hours=i),
               "interval_end": datetime(2026, 1, 1) + timedelta(hours=i+1), "consumption_wh": 700,
               "quality_status": "VALID"} for i in range(100)]
    target = {"meter_id": "m", "interval_start": datetime(2026, 1, 31, 22, 30),
              "interval_end": datetime(2026, 1, 31, 23, 30), "consumption_wh": 1000, "quality_status": "VALID"}
    source.append(target)
    interval_schema = "meter_id string, interval_start timestamp, interval_end timestamp, consumption_wh long, quality_status string"
    work = args["INTERVAL_TABLE"] + "_recalculation_work"
    spark.sql(f"CREATE TABLE {work} (batch_id string, meter_id string, reading_at timestamp, state string) USING iceberg")
    def save_source():
        spark.createDataFrame(source, interval_schema).writeTo(args["INTERVAL_TABLE"]).using("iceberg").createOrReplace()
    def endpoint(batch, end):
        spark.createDataFrame([(batch, "m", end, "DONE")], "batch_id string, meter_id string, reading_at timestamp, state string").writeTo(work).append()
    save_source()
    endpoint("silver-initial", target["interval_end"])
    reports = {}
    def normalized(rows, dims):
        return {tuple(str(r[k]) for k in dims): tuple(Decimal(str(r[k])) for k in ("consumption_kwh", "energy_cost_eur")) for r in rows}
    def compare(label):
        segments = list(iter_priced_segments((utc_record(r) for r in source), refs))
        for row in segments:
            for field in ("consumption_kwh", "energy_cost_eur"):
                row[field] = row[field].quantize(Decimal("1e-12"), rounding=ROUND_HALF_EVEN)
        expected = gold_products(segments)
        for (key, dims), product in zip(refresh.PRODUCTS, ("daily_site_energy", "daily_customer_energy", "monthly_customer_energy")):
            actual = [r.asDict() for r in spark.table(args[key]).collect()]
            assert len(actual) == len(normalized(actual, dims)), "Duplicate aggregate group"
            assert normalized(actual, dims) == normalized(expected[product], dims), label
        # Verify the entire priced content, including old segment removal.
        wanted = sorted(repr(tuple(v.replace(tzinfo=None) if isinstance(v, datetime) else v for v in row))
                        for row in price_partition(source, refs))
        actual = sorted(repr(tuple(r[k] for k in SEGMENT_COLUMNS)) for r in spark.table(args["PRICED_INTERVAL_TABLE"]).collect())
        assert wanted == actual, "Priced rows differ from full rebuild"
        reports[label] = {"segments": len(actual), "full_rebuild_equivalent": True}
        print("GOLD_SCENARIO " + json.dumps({label: reports[label]}), flush=True)
    def run(batch, mode="bounded"):
        args.update(BATCH_ID=batch, REFRESH_MODE=mode)
        result = refresh.price(spark, args)
        result.update(refresh.aggregate(spark, args))
        return result
    run("gold-initial")
    compare("bootstrap")
    wrong = {**args, "DAILY_SITE_TABLE": args["DAILY_SITE_TABLE"] + "_wrong"}
    for action in (refresh.price, refresh.aggregate):
        try:
            action(spark, wrong)
        except ValueError:
            pass
        else:
            raise AssertionError("Retry with changed destinations was not refused")
    spark.sql(f"UPDATE {work} SET state = 'PENDING'")
    try:
        run("gold-pending-silver")
    except ValueError as error:
        assert "Finish Silver" in str(error)
    else:
        raise AssertionError("Pending Silver was not refused")
    spark.sql(f"UPDATE {work} SET state = 'DONE'")
    # Two Silver commits before Gold, late split and changed predecessor context.
    source.remove(target)
    source.extend([{**target, "interval_end": datetime(2026, 1, 31, 23), "consumption_wh": 400},
                   {**target, "interval_start": datetime(2026, 1, 31, 23), "consumption_wh": 800}])
    endpoint("silver-late", datetime(2026, 1, 31, 23))
    endpoint("silver-correction", target["interval_end"])
    save_source()
    args["BATCH_ID"] = "gold-correction"
    original_transition = refresh.transition
    def crash(s, c, b, target_state):
        if target_state == "PRICED":
            raise RuntimeError("injected after priced commit")
        return original_transition(s, c, b, target_state)
    refresh.transition = crash
    try:
        try:
            refresh.price(spark, args)
        except RuntimeError as error:
            assert str(error) == "injected after priced commit"
        else:
            raise AssertionError("Expected injected failure")
    finally:
        refresh.transition = original_transition
    args["BATCH_ID"] = "gold-blocked"
    try:
        refresh.price(spark, args)
    except ValueError as error:
        assert "pending Gold" in str(error)
    else:
        raise AssertionError("Different run was not blocked")
    args["BATCH_ID"] = "gold-correction"
    assert refresh.price(spark, args)["priced_mutations"] == 0
    original_merge = refresh.merge_rows
    def partial(s, table, *values, **kwargs):
        if table == args["DAILY_CUSTOMER_TABLE"]:
            raise RuntimeError("injected after first aggregate commit")
        return original_merge(s, table, *values, **kwargs)
    refresh.merge_rows = partial
    try:
        try:
            refresh.aggregate(spark, args)
        except RuntimeError as error:
            assert str(error) == "injected after first aggregate commit"
        else:
            raise AssertionError("Expected aggregate interruption")
    finally:
        refresh.merge_rows = original_merge
    refresh.aggregate(spark, args)
    compare("late_correction_and_two_commit_recoveries")
    # An absent Silver endpoint must delete previously priced segments and groups.
    source[:] = [r for r in source if r["meter_id"] != "m"]
    save_source()
    endpoint("silver-baseline", datetime(2026, 1, 31, 23))
    endpoint("silver-baseline", target["interval_end"])
    result = run("gold-baseline")
    assert result["priced_mutations"] == 2, result
    compare("baseline_deletes_day_month_groups")
    tables = [args["PRICED_INTERVAL_TABLE"], *(args[k] for k, _ in refresh.PRODUCTS)]
    before = [refresh.snapshot(spark, t) for t in tables]
    result = run("gold-replay")
    assert result["priced_mutations"] == 0 and all(v == 0 for v in result["aggregate_mutations"].values())
    assert before == [refresh.snapshot(spark, t) for t in tables]
    assert run("gold-replay")["aggregate_mutations"] == 0
    compare("replay_preserves_four_target_snapshots")
    refs["customer_site"][1]["customer_id"] = "new-owner"
    save_refs()
    try:
        run("gold-refused-reference")
    except ValueError as error:
        assert "Reference snapshots changed" in str(error)
    else:
        raise AssertionError("Reference change was not rejected")
    assert before == [refresh.snapshot(spark, t) for t in tables]
    run("gold-reference-full", "full")
    compare("explicit_full_reference_ownership_change")
    before = [refresh.snapshot(spark, t) for t in tables]
    source[0]["consumption_wh"] = -1
    save_source()
    try:
        run("gold-invalid", "full")
    except Exception:
        control = args["PRICED_INTERVAL_TABLE"] + "_refresh"
        assert refresh.state(spark, control, "gold-invalid") is None
    else:
        raise AssertionError("Invalid energy was accepted")
    assert before == [refresh.snapshot(spark, t) for t in tables]
    source[0]["consumption_wh"] = 700
    save_source()
    run("gold-fixed-input", "full")
    compare("failed_preparation_releases_run_without_publishing")
    print("GOLD_QUALIFICATION " + json.dumps({"passed": True, "scenarios": reports}), flush=True)


if __name__ == "__main__":
    main()
