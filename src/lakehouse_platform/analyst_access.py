"""Project-scoped analyst governance setup and fail-closed access qualification."""
from contextlib import contextmanager
from copy import deepcopy
import re
from urllib.parse import urlsplit

GOLD = ("daily_site_energy", "daily_customer_energy", "monthly_customer_energy")
SILVER = ("consumption_index", "meter_consumption_interval", "site_meter_history", "customer_site_history",
          "site_contract_history", "tariff_grid_history", "priced_intervals",
          "meter_consumption_interval_recalculation_work", "priced_intervals_stage", "priced_intervals_refresh")


def validate_database(database):
    if not re.fullmatch(r"aws_lakehouse_platform_[a-z0-9_]{3,200}", database):
        raise ValueError("Expected the isolated project database")


def table_resource(account, database, table):
    validate_database(database)
    if not re.fullmatch(r"\d{12}", account) or table not in GOLD:
        raise ValueError("Only the three exact Gold resources may be governed")
    return {"Table": {"CatalogId": account, "DatabaseName": database, "Name": table}}


@contextmanager
def temporary_administrator(lf, principal):
    """Preserve defaults and other admins; remove only our added admin on exit.

    Serialize account-settings maintenance: the LF API has no conditional update.
    Callers must retain the private before/after journal and verify restoration.
    """
    if not re.fullmatch(r"arn:aws:iam::\d{12}:(user|role)/[A-Za-z0-9+=,.@_/-]+", principal):
        raise ValueError("Use a canonical same-account IAM governance principal")
    before = lf.get_data_lake_settings()["DataLakeSettings"]
    before = deepcopy(before)
    admins = before.get("DataLakeAdmins", [])
    added = not any(p["DataLakePrincipalIdentifier"] == principal for p in admins)
    record = {"before": before, "temporarily_added": added, "restored": False}
    if added:
        desired = deepcopy(before)
        desired["DataLakeAdmins"] = [*admins, {"DataLakePrincipalIdentifier": principal}]
        lf.put_data_lake_settings(DataLakeSettings=desired)
    try:
        yield record
    finally:
        if added:
            latest = deepcopy(lf.get_data_lake_settings()["DataLakeSettings"])
            latest["DataLakeAdmins"] = [p for p in latest.get("DataLakeAdmins", []) if p["DataLakePrincipalIdentifier"] != principal]
            lf.put_data_lake_settings(DataLakeSettings=latest)
        record["after"] = lf.get_data_lake_settings()["DataLakeSettings"]
        record["restored"] = record["after"] == before
        if not record["restored"]:
            raise RuntimeError("Lake Formation settings changed concurrently; our added admin was removed, inspect the private journal")


def grants(lf, resource):
    result, token = [], None
    while True:
        args = {"Resource": resource}
        if token:
            args["NextToken"] = token
        page = lf.list_permissions(**args)
        result.extend(page["PrincipalResourcePermissions"])
        token = page.get("NextToken")
        if not token:
            return result


def is_legacy(principal):
    return principal.replace("_", "").upper() == "IAMALLOWEDPRINCIPALS"


def seal_gold(lf, account, database, analyst, writer, governance, cleanup):
    """Revoke only legacy group grants on exact Gold tables, after delegation."""
    for table in GOLD:
        resource = table_resource(account, database, table)
        rows = grants(lf, resource)
        required = {analyst: {"SELECT", "DESCRIBE"}, writer: {"ALL"}, governance: {"ALL"}, cleanup: {"ALL"}}
        for principal, permissions in required.items():
            matching = [r for r in rows if r["Principal"]["DataLakePrincipalIdentifier"] == principal]
            if not permissions <= {p for r in matching for p in r["Permissions"]}:
                raise ValueError("Required explicit Gold grants are missing; legacy access was not removed")
            if principal in (governance, cleanup) and not any("ALL" in r.get("PermissionsWithGrantOption", []) for r in matching):
                raise ValueError("Grantable governance/cleanup rights required before sealing Gold")
        for row in rows:
            if is_legacy(row["Principal"]["DataLakePrincipalIdentifier"]):
                lf.revoke_permissions(Principal=row["Principal"], Resource=resource, Permissions=row["Permissions"],
                                      PermissionsWithGrantOption=row.get("PermissionsWithGrantOption", []))
        if any(is_legacy(r["Principal"]["DataLakePrincipalIdentifier"]) for r in grants(lf, resource)):
            raise ValueError("Legacy IAM group still bypasses named Gold grants")


def gold_location(bucket, database, table, location):
    validate_database(database)
    uri = urlsplit(location)
    paths = (f"/warehouse/{database}/{table}", f"/warehouse/{database}.db/{table}")
    if table not in GOLD or uri.scheme != "s3" or uri.netloc != bucket or uri.path.rstrip("/") not in paths or uri.query or uri.fragment:
        raise ValueError("Gold location must stay inside the exact project/table warehouse prefix")
    return location.rstrip("/")


def access_addresses():
    result = {f'{kind}[0]' for kind in ("aws_iam_role.analyst", "aws_iam_role.lakeformation_data", "aws_iam_role_policy.analyst",
               "aws_iam_role_policy.lakeformation_data", "aws_athena_workgroup.analyst", "aws_lakeformation_permissions.cleanup_database",
               "aws_lakeformation_permissions.governance_database", "aws_lakeformation_permissions.analyst_database")}
    for kind in ("aws_lakeformation_resource.gold", "aws_lakeformation_permissions.cleanup_gold", "aws_lakeformation_permissions.governance_gold",
                 "aws_lakeformation_permissions.writer_gold", "aws_lakeformation_permissions.operator_gold", "aws_lakeformation_permissions.analyst_gold",
                 "aws_lakeformation_permissions.cleanup_location", "aws_lakeformation_permissions.governance_location", "aws_lakeformation_permissions.writer_location"):
        result.update(f'{kind}["{table}"]' for table in GOLD)
    return frozenset(result)


def validate_delete(address, before, demo_id):
    """Validate untaggable LF pointers and exact ephemeral identity names."""
    if address not in access_addresses():
        raise ValueError("Unknown analyst cleanup instance")
    prefix = f"aws-lakehouse-platform-{demo_id}"
    database = prefix.replace("-", "_")
    if address.startswith("aws_iam_role."):
        expected = prefix + ("-analyst" if ".analyst[" in address else "-lakeformation-data")
        if before.get("name") != expected:
            raise ValueError("Unexpected analyst identity name")
        return
    if address.startswith("aws_iam_role_policy."):
        expected = prefix + ("-analyst" if ".analyst[" in address else "-lakeformation-data")
        if before.get("role") != expected or before.get("name") != expected:
            raise ValueError("Only the exact analyst/data-role inline policies may be removed")
        return
    if address.startswith("aws_athena_workgroup."):
        if before.get("name") != prefix + "-analyst":
            raise ValueError("Unexpected analyst workgroup")
        return
    table = next((name for name in GOLD if f'["{name}"]' in address), None)
    def location(arn):
        match = re.fullmatch(r"arn:aws:s3:::(aws-lakehouse-platform-" + re.escape(demo_id) + r"-[a-f0-9]{8})/(.+)", arn or "")
        if not match:
            raise ValueError("Unexpected registered data location")
        gold_location(match[1], database, table, f"s3://{match[1]}/{match[2]}")
    if address.startswith("aws_lakeformation_resource."):
        location(before.get("arn"))
        if (not re.fullmatch(r"arn:aws:iam::\d{12}:role/" + re.escape(prefix) + "-lakeformation-data", before.get("role_arn", "")) or
                before.get("hybrid_access_enabled") or before.get("use_service_linked_role")):
            raise ValueError("Only the dedicated standard Gold registration may be removed")
        return
    kind = address.split(".")[1].split("[")[0]
    actor = kind.split("_")[0]
    principal = before.get("principal", "")
    identity = re.fullmatch(r"arn:aws:iam::(\d{12}):(user|role)/(.+)", principal)
    expected_name = {"cleanup": "aws-lakehouse-platform-demo-cleanup-build", "writer": "aws-lakehouse-platform-demo-glue-silver",
                     "operator": "aws-lakehouse-platform-demo-terraform-operator",
                     "analyst": prefix + "-analyst"}.get(actor)
    if not identity or (expected_name and (identity[2] != "role" or identity[3] != expected_name)):
        raise ValueError("Unexpected LF cleanup principal")
    block_name = "data_location" if kind.endswith("_location") else "database" if kind.endswith("_database") else "table"
    blocks = before.get(block_name, [])
    if len(blocks) != 1 or before.get("catalog_resource") or any(before.get(k) for k in
            ("lf_tag", "lf_tag_policy", "table_with_columns", "data_cells_filter", *({"table", "database", "data_location"} - {block_name}))):
        raise ValueError("LF cleanup requires one exact named resource")
    block = blocks[0]
    if block.get("catalog_id") != identity[1] or before.get("catalog_id") not in (None, identity[1]):
        raise ValueError("LF principal and resource catalogs must match")
    if block_name == "table":
        if block.get("database_name") != database or block.get("name") != table or block.get("wildcard"):
            raise ValueError("LF cleanup is restricted to this demo's three exact Gold tables")
        expected = ({"SELECT", "DESCRIBE"} if actor in ("analyst", "operator") else
                    {"ALL", "ALTER", "DELETE", "DESCRIBE", "DROP", "INSERT", "SELECT"} if actor == "writer" else {"ALL"})
    elif block_name == "database":
        if block.get("name") != database:
            raise ValueError("Unexpected LF database")
        expected = {"DESCRIBE"}
    else:
        location(block.get("arn"))
        expected = {"DATA_LOCATION_ACCESS"}
    grantable = set(before.get("permissions_with_grant_option") or [])
    if set(before.get("permissions") or []) != expected or not grantable <= expected:
        raise ValueError("Unexpected LF permissions in destroy plan")


def authorization_failure(message):
    value = message.casefold()
    return any(word in value for word in ("accessdenied", "access denied", "not authorized", "permission_denied",
                                          "insufficient lake formation permission", "insufficient permissions to execute the query",
                                          "does not have any privilege on specified resource"))


def expect_query_denied(athena, database, workgroup, sql, *, confirmed_catalog_denial=False):
    from .qualification import query_rows
    try:
        query_rows(athena, database, workgroup, sql)
    except RuntimeError as error:
        if confirmed_catalog_denial and "TABLE_NOT_FOUND" in str(error):
            return {"denied": True, "authorization_error": True, "catalog_masked_not_found": True}
        if not authorization_failure(str(error)):
            raise
        return {"denied": True, "authorization_error": True}
    raise AssertionError("An unauthorized query succeeded")


def expect_catalog_denied(glue, database, table):
    from botocore.exceptions import ClientError
    try:
        glue.get_table(DatabaseName=database, Name=table)
    except ClientError as error:
        if error.response["Error"]["Code"] != "AccessDeniedException" or not authorization_failure(error.response["Error"].get("Message", "")):
            raise
        return {"denied": True, "code": "AccessDeniedException"}
    raise AssertionError("Analyst unexpectedly obtained internal table metadata")


def expect_object_denied(s3, bucket, key):
    from botocore.exceptions import ClientError
    try:
        response = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-0")
    except ClientError as error:
        if error.response["Error"]["Code"] != "AccessDenied" or error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 403:
            raise
        return {"denied": True, "code": "AccessDenied", "http_status": 403}
    response["Body"].close()
    raise AssertionError("Direct data access unexpectedly succeeded")


def qualify_access(operator, analyst, lf, config, expected, bronze_key):
    """Clients use real assumed-role credentials; no policy simulation counts as proof."""
    from .qualification import DIMENSIONS, compare_product, query_rows
    bucket, database, account, workgroup = (config[k] for k in ("bucket", "database", "account", "workgroup"))
    validate_database(database)
    if set(expected) != set(GOLD) or any(not expected[t] for t in GOLD):
        raise ValueError("Supply independent, nonempty expected results for all three Gold products")
    role_name = database.replace("_", "-") + "-analyst"
    if config["role_arn"] != f"arn:aws:iam::{account}:role/{role_name}":
        raise ValueError("Analyst role and database must identify the same project demo")
    actual_role = analyst["sts"].get_caller_identity()["Arn"]
    if not actual_role.startswith(f"arn:aws:sts::{account}:assumed-role/{role_name}/"):
        raise ValueError("Access tests must actually use the expected analyst role")
    work = analyst["athena"].get_work_group(WorkGroup=workgroup)["WorkGroup"]["Configuration"]
    if (not work.get("EnforceWorkGroupConfiguration") or work["ResultConfiguration"]["OutputLocation"] != f"s3://{bucket}/athena-results/analyst/" or
            work["ResultConfiguration"]["EncryptionConfiguration"]["EncryptionOption"] != "SSE_S3" or
            work.get("EngineVersion", {}).get("SelectedEngineVersion") != "Athena engine version 3" or
            not 10_000_000 <= work.get("BytesScannedCutoffPerQuery", 0) <= 104857600):
        raise ValueError("Analyst workgroup must isolate encrypted results and enforce the scan/engine configuration")
    catalog = {}
    for name in (*GOLD, *SILVER):
        catalog[name] = operator["glue"].get_table(DatabaseName=database, Name=name)["Table"]
    for name in GOLD:
        location = gold_location(bucket, database, name, catalog[name]["StorageDescriptor"]["Location"])
        registration = lf.describe_resource(ResourceArn=location.replace("s3://", "arn:aws:s3:::"))["ResourceInfo"]
        if registration["RoleArn"] != config["data_role_arn"] or registration.get("HybridAccessEnabled", False):
            raise ValueError("Gold data must use its dedicated standard LF registration")
        rows = grants(lf, table_resource(account, database, name))
        if any(is_legacy(r["Principal"]["DataLakePrincipalIdentifier"]) for r in rows):
            raise ValueError("Legacy IAM group still bypasses Gold LF permissions")
        mine = [r for r in rows if r["Principal"]["DataLakePrincipalIdentifier"] == config["role_arn"]]
        if {p for r in mine for p in r["Permissions"]} != {"SELECT", "DESCRIBE"} or any(r.get("PermissionsWithGrantOption") for r in mine):
            raise ValueError("Analyst requires read-only Gold grants without grant options")
    sqls, positive = {}, {}
    def query_gold(name):
        columns = ", ".join(f"CAST({field} AS varchar) AS {field}" for field in (*DIMENSIONS[name], "consumption_kwh", "energy_cost_eur"))
        sql = f'SELECT {columns} FROM "{database}"."{name}"'
        rows, statistics = query_rows(analyst["athena"], database, workgroup, sql)
        if statistics.get("ResultReuseInformation", {}).get("ReusedPreviousResult") is not False:
            raise ValueError("Access qualification cannot rely on reused query results")
        compared = compare_product(name, expected[name], rows)
        if not compared["passed"]:
            raise ValueError("Analyst Gold content differs from the independent oracle")
        return sql, compared
    for name in GOLD:
        sqls[name], positive[name] = query_gold(name)
    denied_sql = {}
    for name in SILVER:
        catalog_denial = expect_catalog_denied(analyst["glue"], database, name)
        denied_sql[name] = {"catalog": catalog_denial, "athena": expect_query_denied(analyst["athena"], database, workgroup,
                            f'SELECT COUNT(*) FROM "{database}"."{name}"', confirmed_catalog_denial=True), "exists_for_operator": True}
    if not bronze_key.startswith("landing/") or ".." in bronze_key:
        raise ValueError("Bronze probe must be an existing project landing object")
    probes = {"bronze": bronze_key}
    for label, name in (("silver", "meter_consumption_interval"), ("gold_direct", "daily_site_energy")):
        uri = urlsplit(catalog[name]["StorageDescriptor"]["Location"])
        if uri.scheme != "s3" or uri.netloc != bucket or not uri.path.startswith("/warehouse/"):
            raise ValueError("Probe location escapes the project warehouse")
        prefix = uri.path.lstrip("/").rstrip("/") + "/data/"
        response = operator["s3"].list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
        keys = [r["Key"] for r in response.get("Contents", []) if r["Key"].endswith(".parquet")]
        if not keys:
            raise ValueError("An actual existing data file is required for the direct S3 denial probe")
        probes[label] = keys[0]
    denied_s3 = {}
    for label, key in probes.items():
        operator["s3"].get_object(Bucket=bucket, Key=key, Range="bytes=0-0")["Body"].close()
        denied_s3[label] = expect_object_denied(analyst["s3"], bucket, key)
    # SELECT alone is revoked, leaving DESCRIBE and all IAM policies unchanged.
    target = GOLD[0]
    permission = {"Principal": {"DataLakePrincipalIdentifier": config["role_arn"]}, "Resource": table_resource(account, database, target), "Permissions": ["SELECT"]}
    lf.revoke_permissions(**permission)
    try:
        revoked = expect_query_denied(analyst["athena"], database, workgroup, sqls[target])
    finally:
        lf.grant_permissions(**permission)
    _, restored = query_gold(target)
    return {"passed": True, "gold_products": positive, "silver_and_internal_sql_denials": denied_sql,
            "direct_s3_denials": denied_s3, "gold_select_revocation_denied": revoked, "gold_select_restored": restored["passed"],
            "results_reused": False, "temporary_credentials_used": True,
            "authorization_layers": {"gold": "Lake Formation SELECT with registered Gold-only S3 credentials",
                                     "silver_sql": "IAM Glue catalog scope excludes Silver and internal tables",
                                     "bronze_silver_and_gold_direct_s3": "Analyst IAM denies direct landing/warehouse object access"}}
