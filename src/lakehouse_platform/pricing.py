"""Shared local/Glue Gold pricing, on half-open UTC segments."""

from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
VALID_STATUSES = {"VALID", "VALID_WITH_WARNING"}
CONTRACTS = {"BASE", "WEEKEND", "PEAK_OFF_PEAK"}


class PricingRejected(ValueError):
    """Incomplete or ambiguous pricing must never produce a partial bill."""


def _dt(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if value.tzinfo is None or value.utcoffset() is None:
        raise PricingRejected("Business timestamps must include a timezone")
    return value.astimezone(UTC)


def _active(rows, field, value, at):
    matches = [r for r in rows if r[field] == value and _dt(r["valid_from"]) <= at
               and (r.get("valid_to") is None or at < _dt(r["valid_to"]))]
    if len(matches) != 1:
        raise PricingRejected(f"Expected one active {field} relationship for {value!r} at {at.isoformat()}")
    return matches[0]


def _price(rules, contract_type, at):
    if contract_type not in CONTRACTS:
        raise PricingRejected(f"Unknown contract type {contract_type!r}")
    local = at.astimezone(PARIS)
    matches = []
    for rule in rules:
        if rule["contract_type"] != contract_type or not (_dt(rule["valid_from"]) <= at
                and (rule.get("valid_to") is None or at < _dt(rule["valid_to"]))):
            continue
        scope = rule["day_scope"]
        if scope not in {"ALL_DAYS", "WEEKDAY", "WEEKEND"}:
            raise PricingRejected(f"Unknown day scope {scope!r}")
        if (scope == "WEEKDAY" and local.weekday() >= 5) or (scope == "WEEKEND" and local.weekday() < 5):
            continue
        start, end = rule.get("time_from_local"), rule.get("time_to_local")
        if (start is None) != (end is None):
            raise PricingRejected("Both tariff window endpoints are required")
        if start is not None:
            start_t, end_t = time.fromisoformat(start), time.fromisoformat(end)
            if start_t == end_t:
                raise PricingRejected("Equal window endpoints are ambiguous; use null for a full day")
            current = local.time()
            if not ((start_t < end_t and start_t <= current < end_t)
                    or (start_t > end_t and (current >= start_t or current < end_t))):
                continue
        price = Decimal(str(rule["price_eur_per_kwh"]))
        if not price.is_finite() or price < 0:
            raise PricingRejected("Tariff prices must be finite and non-negative")
        matches.append(price)
    if len(matches) != 1:
        raise PricingRejected(f"Expected one tariff for {contract_type} at {at.isoformat()}; got {len(matches)}")
    return matches[0]


def _boundaries(start, end, references):
    """Include reference changes, local days/windows and both DST folds."""
    boundaries = {start, end}
    clock_times = {time(0)}
    for rows in references.values():
        for row in rows:
            for field in ("valid_from", "valid_to"):
                if row.get(field) is not None:
                    at = _dt(row[field])
                    if start < at < end:
                        boundaries.add(at)
    for rule in references["tariff_grid"]:
        for field in ("time_from_local", "time_to_local"):
            if rule.get(field) is not None:
                clock_times.add(time.fromisoformat(rule[field]))
    day = start.astimezone(PARIS).date()
    last = end.astimezone(PARIS).date()
    while day <= last:
        for clock in clock_times:
            wall = datetime.combine(day, clock)
            for fold in (0, 1):
                at = wall.replace(tzinfo=PARIS, fold=fold).astimezone(UTC)
                if at.astimezone(PARIS).replace(tzinfo=None) == wall and start < at < end:
                    boundaries.add(at)
        day += timedelta(days=1)
    # Offset changes can jump over a tariff edge in the missing DST hour.
    cursor = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while cursor < end:
        boundaries.add(cursor)
        cursor += timedelta(hours=1)
    return sorted(boundaries)


def iter_priced_segments(intervals, references):
    """Uniform elapsed-time allocation; streaming intervals, bounded references.

    The final segment absorbs Decimal division residue, conserving energy.
    A rejected interval raises before any of its segments are yielded.
    """
    for interval in intervals:
        if interval["quality_status"] not in VALID_STATUSES:
            continue
        start, end = _dt(interval["interval_start"]), _dt(interval["interval_end"])
        wh = Decimal(str(interval["consumption_wh"]))
        if end <= start or not wh.is_finite() or wh < 0:
            raise PricingRejected("Intervals require positive elapsed time and non-negative finite energy")
        total = Decimal((end - start) // timedelta(microseconds=1))
        boundaries = _boundaries(start, end, references)
        allocated = Decimal(0)
        segments = []
        for left, right in zip(boundaries, boundaries[1:]):
            meter = _active(references["site_meter"], "meter_id", interval["meter_id"], left)
            site = meter["site_id"]
            contract = _active(references["site_contract"], "site_id", site, left)
            customer = _active(references["customer_site"], "site_id", site, left)
            rate = _price(references["tariff_grid"], contract["contract_type"], left)
            energy = (wh - allocated if right == end else
                      wh * Decimal((right - left) // timedelta(microseconds=1)) / total)
            allocated += energy
            segments.append({
                "meter_id": interval["meter_id"], "customer_id": customer["customer_id"], "site_id": site,
                "interval_start": start.isoformat(), "interval_end": end.isoformat(),
                "segment_start": left.isoformat(), "segment_end": right.isoformat(),
                "local_date": left.astimezone(PARIS).date().isoformat(),
                "contract_type": contract["contract_type"], "price_eur_per_kwh": rate,
                "consumption_kwh": energy / Decimal(1000), "energy_cost_eur": energy / Decimal(1000) * rate,
                "allocation_method": "PRO_RATA_TIME", "quality_status": interval["quality_status"],
            })
        yield from segments


def price_intervals(intervals, references):
    return list(iter_priced_segments(intervals, references))


def aggregate_energy(rows, dimensions):
    totals = defaultdict(lambda: {"consumption_kwh": Decimal(0), "energy_cost_eur": Decimal(0)})
    for row in rows:
        key = tuple(row[field] for field in dimensions)
        for field in ("consumption_kwh", "energy_cost_eur"):
            totals[key][field] += row[field]
    return [{**dict(zip(dimensions, key)), **value} for key, value in sorted(totals.items())]


def daily_customer_energy(rows):
    return aggregate_energy(rows, ("customer_id", "local_date"))


def gold_products(rows):
    rows = list(rows)
    return {
        "daily_site_energy": aggregate_energy(rows, ("customer_id", "site_id", "local_date")),
        "daily_customer_energy": daily_customer_energy(rows),
        "monthly_customer_energy": aggregate_energy(
            ({**r, "local_month": r["local_date"][:7]} for r in rows), ("customer_id", "local_month")),
    }
