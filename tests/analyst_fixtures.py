"""Synthetic Terraform before-state records for destruction boundary tests."""
def analyst_before(address, demo):
    prefix = "aws-lakehouse-platform-" + demo
    database, account = prefix.replace("-", "_"), "000000000000"
    table = next((name for name in ("daily_site_energy", "daily_customer_energy", "monthly_customer_energy") if f'["{name}"]' in address), None)
    location = f"arn:aws:s3:::{prefix}-aabbccdd/warehouse/{database}.db/{table}"
    if address.startswith("aws_iam_role.") or address.startswith("aws_iam_role_policy."):
        name = prefix + ("-analyst" if ".analyst[" in address else "-lakeformation-data")
        return {"name": name, "role": name}
    if address.startswith("aws_athena_workgroup."):
        return {"name": prefix + "-analyst"}
    if address.startswith("aws_lakeformation_resource."):
        return {"arn": location, "role_arn": f"arn:aws:iam::{account}:role/{prefix}-lakeformation-data",
                "hybrid_access_enabled": False, "use_service_linked_role": False}
    kind = address.split(".")[1].split("[")[0]
    actor = kind.split("_")[0]
    principal = {"cleanup": "role/aws-lakehouse-platform-demo-cleanup-build", "governance": "user/governance",
                 "writer": "role/aws-lakehouse-platform-demo-glue-silver", "operator": "role/aws-lakehouse-platform-demo-terraform-operator",
                 "analyst": "role/" + prefix + "-analyst"}[actor]
    if kind.endswith("_database"):
        block, resource, permissions = "database", {"name": database, "catalog_id": account}, ["DESCRIBE"]
    elif kind.endswith("_location"):
        block, resource, permissions = "data_location", {"arn": location, "catalog_id": account}, ["DATA_LOCATION_ACCESS"]
    else:
        block, resource = "table", {"database_name": database, "name": table, "catalog_id": account, "wildcard": False}
        permissions = (["SELECT", "DESCRIBE"] if actor in ("analyst", "operator") else
                       ["ALL", "ALTER", "DELETE", "DESCRIBE", "DROP", "INSERT", "SELECT"] if actor == "writer" else ["ALL"])
    grantable = permissions if actor in ("cleanup", "governance") or (actor == "writer" and block == "table") else []
    return {"principal": f"arn:aws:iam::{account}:{principal}", "catalog_id": account,
            "permissions": permissions, "permissions_with_grant_option": grantable, block: [resource]}
