from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import random
import unittest
from unittest.mock import Mock, patch
from tempfile import TemporaryDirectory

from lakehouse_platform.intervals import ConsumptionIndex, InvalidIndexSequence, derive_consumption_interval
from lakehouse_platform.recalculation import plan_index_updates
from lakehouse_platform.bounded_fixture import write_bounded_fixture
from lakehouse_platform.landing import plan_landing_upload

START = datetime(2026, 1, 1, tzinfo=UTC)


def reading(hour, *, meter="meter-a", version=1, value=None, context="NORMAL", rollovers=0):
    return {"meter_id":meter,"reading_at":START+timedelta(hours=hour),
            "meter_index_wh":int(1000+hour*1000) if value is None else value,
            "rollover_count":rollovers,"reading_context":context,"source_version":version,
            "received_at":START+timedelta(days=10),"schema_version":1}


def interval(previous, current):
    def index(row):
        return ConsumptionIndex(row["meter_id"],row["reading_at"],row["meter_index_wh"],row["rollover_count"],row["reading_context"])
    return derive_consumption_interval(index(previous) if previous else None,index(current))


def full_rebuild(rows):
    result, previous = {}, {}
    for row in sorted(rows,key=lambda r:(r["meter_id"],r["reading_at"])):
        value = interval(previous.get(row["meter_id"]),row)
        if value is not None:
            result[row["meter_id"],row["reading_at"]] = value
        previous[row["meter_id"]] = row
    return result


def apply_plan(rows, intervals, incoming):
    plan = plan_index_updates(rows,incoming)
    stored = {(r["meter_id"],r["reading_at"]):r for r in rows}
    stored.update({(r["meter_id"],r["reading_at"]):r for r in plan.upserts})
    result = dict(intervals)
    for previous,current in plan.interval_pairs:
        key = current["meter_id"],current["reading_at"]
        value = interval(previous,current)
        if value is None:
            result.pop(key,None)
        else:
            result[key] = value
    return list(stored.values()),result,plan


class RecalculationTests(unittest.TestCase):
    def test_live_fixture_is_deterministic_manifest_backed_and_preserves_existing_outputs(self):
        with TemporaryDirectory() as directory:
            first,second = Path(directory)/"first",Path(directory)/"second"
            counts = write_bounded_fixture(first)
            self.assertEqual(counts,write_bounded_fixture(second))
            self.assertEqual(2000,counts["initial"])
            for name in counts:
                planned = plan_landing_upload(first/name,"bounded-"+name)
                self.assertEqual(2,len(planned))
                self.assertEqual((first/name/"consumption_index.jsonl").read_bytes(),
                                 (second/name/"consumption_index.jsonl").read_bytes())
            with self.assertRaises(FileExistsError):
                write_bounded_fixture(first)

    def test_sequence_invalid_correction_is_rejected_before_any_plan_can_be_applied(self):
        rows = [reading(i) for i in range(3)]
        for incoming in (reading(1,version=2,value=500), reading(1,version=2,value=5000),
                         {**reading(1,version=2),"rollover_count":1}):
            with self.subTest(incoming=incoming), self.assertRaises(InvalidIndexSequence):
                plan_index_updates(rows,[incoming])

    def test_single_correction_touches_only_itself_and_successor(self):
        rows = [reading(i) for i in range(1000)] + [reading(i,meter="other") for i in range(1000)]
        updated,actual,plan = apply_plan(rows,full_rebuild(rows),[reading(500,version=2,value=501100)])
        self.assertEqual([500,501],[(current["reading_at"]-START).total_seconds()/3600 for previous,current in plan.interval_pairs])
        self.assertEqual(full_rebuild(updated),actual)
        self.assertEqual(2,len(plan.interval_keys))

    def test_late_insert_changes_old_successor_and_new_endpoint(self):
        rows = [reading(0),reading(2)]
        updated,actual,plan = apply_plan(rows,full_rebuild(rows),[reading(1)])
        self.assertEqual(2,len(plan.interval_pairs))
        self.assertEqual(full_rebuild(updated),actual)

    def test_append_first_last_and_multiple_adjacent_changes(self):
        for incoming in ([reading(3)],[reading(-1,value=0)],
                         [reading(1,version=2,value=2100),reading(1.5,value=2500)]):
            rows = [reading(i) for i in range(3)]
            updated,actual,plan = apply_plan(rows,full_rebuild(rows),incoming)
            self.assertEqual(full_rebuild(updated),actual)
            self.assertLessEqual(len(plan.interval_pairs),2*len(plan.upserts))

    def test_replay_and_lower_version_produce_no_work(self):
        rows = [reading(0),reading(1,version=2)]
        for incoming in (rows,[reading(1,version=1)],[{**rows[1],"received_at":START+timedelta(days=20)}]):
            plan = plan_index_updates(rows,incoming)
            self.assertEqual((),plan.upserts)
            self.assertEqual((),plan.interval_pairs)

    def test_baseline_correction_removes_obsolete_interval(self):
        rows = [reading(i) for i in range(3)]
        updated,actual,plan = apply_plan(rows,full_rebuild(rows),[reading(1,version=2,context="INSTALLATION_BASELINE")])
        self.assertNotIn(("meter-a",START+timedelta(hours=1)),actual)
        self.assertEqual(full_rebuild(updated),actual)
        self.assertEqual(2,len(plan.interval_pairs))

    def test_declared_multiple_rollovers_match_business_oracle(self):
        rows = [reading(0,value=999998900),reading(1,value=250,rollovers=1),reading(2,value=500)]
        updated,actual,plan = apply_plan(rows,full_rebuild(rows),[reading(1,value=250,rollovers=2,version=2)])
        self.assertEqual(full_rebuild(updated),actual)
        self.assertEqual(999999352,actual["meter-a",START+timedelta(hours=1)].consumption_wh)

    def test_random_valid_corrections_equal_full_rebuild_without_other_keys_changing(self):
        rng = random.Random(7)
        rows = [reading(i) for i in range(100)]
        actual = full_rebuild(rows)
        for _ in range(30):
            hour = rng.randrange(1,99)
            before = dict(actual)
            old = next(r for r in rows if r["reading_at"]==START+timedelta(hours=hour))
            incoming = reading(hour,version=old["source_version"]+1,value=1000+hour*1000+rng.randrange(-100,100))
            rows,actual,plan = apply_plan(rows,actual,[incoming])
            self.assertEqual(full_rebuild(rows),actual)
            self.assertTrue(all(actual[k]==v for k,v in before.items() if k not in plan.interval_keys))

    def test_batch_deduplication_and_equivalent_utc_offsets(self):
        existing = [{**reading(0),"reading_at":"2026-01-01T00:00:00Z"}]
        replay = {**existing[0],"reading_at":"2026-01-01T01:00:00+01:00"}
        self.assertFalse(plan_index_updates(existing,[replay]).upserts)
        incoming = [reading(1),reading(1,version=2,value=2100),reading(1,version=1)]
        self.assertEqual(2,plan_index_updates(existing,incoming).upserts[0]["source_version"])


spec = importlib.util.spec_from_file_location("silver_job",Path(__file__).parents[1]/"jobs/silver_process_indexes.py")
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class SilverCommitProtocolTests(unittest.TestCase):
    def run_with_steps(self, *, changed=1, pending=2, failure=False):
        calls, effective = [],Mock()
        def prepare(*args):
            calls.append("durable_work")
            return effective,changed
        def merge(*args):
            calls.append("indexes")
        def apply(*args):
            calls.append("intervals")
            if failure:
                raise RuntimeError("Injected interval commit failure")
            return pending,pending
        with patch.object(job,"prepare_work",side_effect=prepare), patch.object(job,"merge_indexes",side_effect=merge),\
             patch.object(job,"apply_intervals",side_effect=apply), patch.object(job,"finish_work",side_effect=lambda *args:calls.append("done")):
            if failure:
                with self.assertRaises(RuntimeError):
                    job.run_batch(Mock(),{"BATCH_ID":"test-batch"},"work",Mock())
                result = None
            else:
                result = job.run_batch(Mock(),{"BATCH_ID":"test-batch"},"work",Mock())
        effective.unpersist.assert_called_once()
        return calls,result

    def test_work_precedes_indexes_and_done_follows_interval_commit(self):
        calls,result = self.run_with_steps()
        self.assertEqual(["durable_work","indexes","intervals","done"],calls)

    def test_failed_interval_commit_leaves_pending_work_for_retry(self):
        calls,_ = self.run_with_steps(failure=True)
        self.assertEqual(["durable_work","indexes","intervals"],calls)
        calls,result = self.run_with_steps(changed=0,pending=2)
        self.assertEqual("done",calls[-1])
        self.assertEqual(0,result["changed_indexes"])
        self.assertEqual(2,result["recalculated_interval_keys"])

    def test_unchanged_replay_does_not_rewrite_work_state(self):
        calls,result = self.run_with_steps(changed=0,pending=0)
        self.assertNotIn("done",calls)
        self.assertEqual(0,result["interval_mutations"])

    def test_job_rejects_conflicting_tables_and_unsafe_batch_identifiers(self):
        args={"BATCH_ID":"test-batch","SILVER_INDEX_TABLE":"glue_catalog.demo.idx",
              "SILVER_INTERVAL_TABLE":"glue_catalog.demo.intervals","QUARANTINE_TABLE":"glue_catalog.demo.quarantine"}
        self.assertEqual("glue_catalog.demo.intervals_recalculation_work",job.validate_arguments(args))
        for overrides in ({"BATCH_ID":"x' OR 1=1"},{"SILVER_INDEX_TABLE":args["QUARANTINE_TABLE"]},
                          {"SILVER_INDEX_TABLE":"glue_catalog.demo.intervals_recalculation_work"},
                          {"QUARANTINE_TABLE":"glue_catalog.demo.x;DROP TABLE y"}):
            with self.subTest(overrides=overrides),self.assertRaises(ValueError):
                job.validate_arguments({**args,**overrides})
