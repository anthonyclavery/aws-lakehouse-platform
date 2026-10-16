"""Pure business rules for deriving consumption from cumulative indexes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class InvalidIndexSequence(ValueError):
    """Raised when two indexes cannot form a valid consumption interval."""


@dataclass(frozen=True)
class ConsumptionIndex:
    meter_id: str
    reading_at: datetime
    meter_index_wh: int
    rollover_count: int = 0
    reading_context: str = "NORMAL"


@dataclass(frozen=True)
class ConsumptionInterval:
    meter_id: str
    interval_start: datetime
    interval_end: datetime
    consumption_wh: int
    quality_status: str
    allocation_method: str


def derive_consumption_interval(
    previous: ConsumptionIndex | None,
    current: ConsumptionIndex,
    *,
    register_max_wh: int = 999_999_000,
    expected_cadence_seconds: int | None = None,
) -> ConsumptionInterval | None:
    """Derive one interval from two validated cumulative meter indexes.

    The first observed index and an installation baseline intentionally produce
    no interval. A lower register value requires an explicitly declared
    rollover. Irregular but valid durations are surfaced as a warning.
    """
    if current.meter_index_wh < 0:
        raise InvalidIndexSequence("meter index cannot be negative")
    if current.rollover_count < 0:
        raise InvalidIndexSequence("rollover_count cannot be negative")
    if current.reading_context == "INSTALLATION_BASELINE" or previous is None:
        return None
    if previous.meter_id != current.meter_id:
        raise InvalidIndexSequence("indexes must belong to the same meter")
    if current.reading_at <= previous.reading_at:
        raise InvalidIndexSequence("index timestamps must be strictly increasing")
    if previous.meter_index_wh > register_max_wh or current.meter_index_wh > register_max_wh:
        raise InvalidIndexSequence("meter index exceeds the configured register maximum")

    if current.meter_index_wh < previous.meter_index_wh and current.rollover_count == 0:
        raise InvalidIndexSequence("register regression requires a declared rollover")
    if current.meter_index_wh >= previous.meter_index_wh and current.rollover_count > 0:
        raise InvalidIndexSequence("declared rollover is inconsistent with register values")

    if current.rollover_count:
        consumption_wh = (
            (register_max_wh + 1 - previous.meter_index_wh)
            + ((current.rollover_count - 1) * (register_max_wh + 1))
            + current.meter_index_wh
        )
    else:
        consumption_wh = current.meter_index_wh - previous.meter_index_wh

    duration_seconds = (current.reading_at - previous.reading_at).total_seconds()
    irregular = expected_cadence_seconds is not None and duration_seconds != expected_cadence_seconds
    return ConsumptionInterval(
        meter_id=current.meter_id,
        interval_start=previous.reading_at,
        interval_end=current.reading_at,
        consumption_wh=consumption_wh,
        quality_status="VALID_WITH_WARNING" if irregular else "VALID",
        allocation_method="PRO_RATA_TIME" if irregular else "DIRECT",
    )
