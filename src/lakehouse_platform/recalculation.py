"""Local oracle for bounded index updates and their interval endpoints."""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from .intervals import ConsumptionIndex, derive_consumption_interval


def _instant(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Index timestamps must include a timezone")
    return value.astimezone(UTC)


def _key(row):
    return row["meter_id"], _instant(row["reading_at"])


@dataclass(frozen=True)
class RecalculationPlan:
    upserts: tuple
    interval_pairs: tuple

    @property
    def interval_keys(self):
        return tuple(_key(current) for previous, current in self.interval_pairs)


def plan_index_updates(existing, incoming):
    """Only higher versions change stored keys; equal versions replay unchanged.

    Input rows are value-valid Silver indexes. The oracle discovers neighbours
    in memory; it does not claim bounded physical reads of an Iceberg table.
    """
    snapshot = {_key(row): dict(row) for row in existing}
    latest = {}
    for row in incoming:
        key = _key(row)
        prior = latest.get(key)
        if prior is None or (int(row["source_version"]), _instant(row["received_at"])) > (
                int(prior["source_version"]), _instant(prior["received_at"])):
            latest[key] = dict(row)
    changed = {key:row for key,row in latest.items()
               if key not in snapshot or int(row["source_version"]) > int(snapshot[key]["source_version"])}
    snapshot.update(changed)
    changed_meters = {meter for meter, at in changed}
    times = defaultdict(list)
    for meter, at in snapshot:
        if meter in changed_meters:
            times[meter].append(at)
    for values in times.values():
        values.sort()
    affected = set(changed)
    for meter, at in changed:
        values = times[meter]
        index = bisect_left(values, at)
        if index + 1 < len(values):
            affected.add((meter, values[index+1]))
    pairs = []
    for meter, at in sorted(affected):
        index = bisect_left(times[meter], at)
        previous = snapshot[(meter, times[meter][index-1])] if index else None
        def point(row):
            return ConsumptionIndex(row["meter_id"], _instant(row["reading_at"]), row["meter_index_wh"],
                                    row["rollover_count"], row["reading_context"])
        derive_consumption_interval(point(previous) if previous else None, point(snapshot[(meter, at)]))
        pairs.append((previous, snapshot[(meter, at)]))
    return RecalculationPlan(tuple(changed[key] for key in sorted(changed)), tuple(pairs))
