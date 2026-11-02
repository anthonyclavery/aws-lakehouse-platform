"""Gold energy and price calculation using temporal reference rows."""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo


PARIS = ZoneInfo("Europe/Paris")


class PricingRejected(ValueError):
    pass


def _dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def _active(rows, field, value, at):
    matches = [r for r in rows if r[field] == value and _dt(r["valid_from"]) <= at and (r.get("valid_to") is None or at < _dt(r["valid_to"]))]
    if len(matches) != 1:
        raise PricingRejected(f"Expected one active {field} relationship for {value!r} at {at.isoformat()}")
    return matches[0]


def _price(rules, contract_type, at):
    local = at.astimezone(PARIS)
    for rule in rules:
        if rule["contract_type"] != contract_type or not (_dt(rule["valid_from"]) <= at and (rule.get("valid_to") is None or at < _dt(rule["valid_to"]))):
            continue
        if rule["day_scope"] == "WEEKDAY" and local.weekday() >= 5 or rule["day_scope"] == "WEEKEND" and local.weekday() < 5:
            continue
        start, end = rule.get("time_from_local"), rule.get("time_to_local")
        if start is None:
            return Decimal(str(rule["price_eur_per_kwh"]))
        current, start_t, end_t = local.time(), time.fromisoformat(start), time.fromisoformat(end)
        if (start_t < end_t and start_t <= current < end_t) or (start_t > end_t and (current >= start_t or current < end_t)):
            return Decimal(str(rule["price_eur_per_kwh"]))
    raise PricingRejected(f"No tariff rule covers {contract_type} at {at.isoformat()}")


def price_intervals(intervals, references):
    """Allocate each valid interval by elapsed hour and return priced site rows."""
    result = []
    for interval in intervals:
        if not interval["quality_status"].startswith("VALID"):
            continue
        start, end = _dt(interval["interval_start"]), _dt(interval["interval_end"])
        meter = _active(references["site_meter"], "meter_id", interval["meter_id"], end)
        contract = _active(references["site_contract"], "site_id", meter["site_id"], end)
        customer = _active(references["customer_site"], "site_id", meter["site_id"], end)
        cursor, total, cost = start, Decimal((end - start).total_seconds()), Decimal("0")
        while cursor < end:
            boundary = min(end, cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            wh = Decimal(str(interval["consumption_wh"])) * Decimal((boundary - cursor).total_seconds()) / total
            cost += (wh / Decimal("1000")) * _price(references["tariff_grid"], contract["contract_type"], cursor)
            cursor = boundary
        result.append({"customer_id": customer["customer_id"], "site_id": meter["site_id"], "interval_end": end.isoformat().replace("+00:00", "Z"), "consumption_kwh": Decimal(str(interval["consumption_wh"])) / Decimal("1000"), "energy_cost_eur": cost})
    return result


def daily_customer_energy(rows):
    totals = defaultdict(lambda: {"consumption_kwh": Decimal("0"), "energy_cost_eur": Decimal("0")})
    for row in rows:
        key = (row["customer_id"], _dt(row["interval_end"]).astimezone(PARIS).date().isoformat())
        totals[key]["consumption_kwh"] += row["consumption_kwh"]
        totals[key]["energy_cost_eur"] += row["energy_cost_eur"]
    return [{"customer_id": key[0], "local_date": key[1], **value} for key, value in sorted(totals.items())]
