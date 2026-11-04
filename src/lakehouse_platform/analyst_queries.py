"""Bounded, read-only queries over the three approved Gold products."""
from datetime import date
from importlib.resources import files
from uuid import UUID

from .analyst_access import validate_database

QUERIES = {"daily-site": "daily_site_energy", "daily-customer": "daily_customer_energy",
           "monthly-customer": "monthly_customer_energy"}


def render_query(name, database, start, end, customer=None):
    validate_database(database)
    if name not in QUERIES:
        raise ValueError("Choose a named Gold query")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first >= last:
        raise ValueError("The start date must precede the exclusive end date")
    if name == "monthly-customer":
        if first.day != 1 or last.day != 1:
            raise ValueError("Monthly queries require first-of-month date boundaries")
        start, end = first.strftime("%Y-%m"), last.strftime("%Y-%m")
    else:
        start, end = first.isoformat(), last.isoformat()
    predicate = ""
    if customer is not None:
        customer = str(UUID(customer))
        predicate = f"AND customer_id = '{customer}'"
    template = files("lakehouse_platform").joinpath("sql", name + ".sql").read_text()
    return template.format(database=database, start=start, end=end, customer_filter=predicate)


def validate_analyst_session(session, values, account):
    """Require the real analyst identity and its isolated, capped result workgroup."""
    database, access = values["glue_database_name"], values["analyst_access"]
    validate_database(database)
    name = database.replace("_", "-") + "-analyst"
    if access["role_arn"] != f"arn:aws:iam::{account}:role/{name}" or access["workgroup"] != name:
        raise ValueError("Analyst role must belong to this exact demo")
    actual = session.client("sts").get_caller_identity()["Arn"]
    if not actual.startswith(f"arn:aws:sts::{account}:assumed-role/{name}/"):
        raise ValueError("Queries must use actual dedicated analyst credentials")
    bucket = values["landing_path"].split("/")[2]
    work = session.client("athena").get_work_group(WorkGroup=access["workgroup"])["WorkGroup"]["Configuration"]
    if (not work.get("EnforceWorkGroupConfiguration") or
            work.get("ResultConfiguration", {}).get("OutputLocation") != f"s3://{bucket}/athena-results/analyst/" or
            work.get("ResultConfiguration", {}).get("EncryptionConfiguration", {}).get("EncryptionOption") != "SSE_S3" or
            work.get("EngineVersion", {}).get("SelectedEngineVersion") != "Athena engine version 3" or
            not 10_000_000 <= work.get("BytesScannedCutoffPerQuery", 0) <= 104857600):
        raise ValueError("Analyst queries require the isolated encrypted, capped engine-3 workgroup")
    return access["workgroup"]
