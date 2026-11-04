"""Explicit AWS adapter for the demo journey; existing jobs and guards stay authoritative."""
import hashlib
import json
import os
import subprocess
import sys
import time

from .analyst_queries import render_query, validate_analyst_session
from .cleanup_context import derive_cleanup_context
from .cleanup_guard import ALLOWED_DELETE_ADDRESSES
from .demo import load_state, read, run_scenarios, write
from .integrated_pipeline import build_integrated_invocations
from .qualification import DIMENSIONS, expected_products, query_rows
from .replay_validation import capture_replay_metadata, compare_replay


def expect_absent(operation, codes):
    from botocore.exceptions import ClientError
    try:
        operation()
    except ClientError as error:
        if error.response["Error"]["Code"] not in codes:
            raise
        return
    raise ValueError("A demo resource still exists after native cleanup; inspect before declaring destruction complete")


def source_digest(repo):
    digest = hashlib.sha256()
    paths = [*repo.glob("terraform/*.tf"), *repo.glob("jobs/*.py"), *repo.glob("scripts/*.py"),
             *repo.glob("src/lakehouse_platform/**/*.py"), *repo.glob("src/lakehouse_platform/sql/*.sql")]
    for path in sorted(paths):
        digest.update(path.relative_to(repo).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def validate_deployment_plan(plan):
    if plan.get("errored"):
        raise ValueError("Terraform plan is errored")
    changes = [row for row in plan.get("resource_changes", [])
               if row["mode"] == "managed" and row["change"]["actions"] != ["no-op"]]
    allowed = ALLOWED_DELETE_ADDRESSES | {"terraform_data.cleanup_context"}
    if any(row["address"] not in allowed or row["change"]["actions"] not in (["create"], ["update"]) for row in changes):
        raise ValueError("Demo plan changes persistent identities, replaces resources or deletes resources; review bootstrap prerequisites separately")
    return [{"address": row["address"], "actions": row["change"]["actions"]} for row in changes]


class AWSJourney:
    def __init__(self, workspace, repo, project_profile, governance_profile, region, *, read_only=False):
        import boto3
        self.workspace, self.repo = workspace, repo
        self.project_profile, self.governance_profile, self.region = project_profile, governance_profile, region
        self.operator = boto3.Session(profile_name=project_profile, region_name=region)
        self.admin = None if read_only else boto3.Session(profile_name=governance_profile, region_name=region)
        identity = self.operator.client("sts").get_caller_identity()
        self.account = identity["Account"]
        if (not identity["Arn"].startswith(f"arn:aws:sts::{self.account}:assumed-role/aws-lakehouse-platform-demo-terraform-operator/")
                or (self.admin and self.admin.client("sts").get_caller_identity()["Account"] != self.account)):
            raise ValueError("Require the exact project operator and same-account governance identities")
        self.environment = {**os.environ, "AWS_PROFILE": project_profile, "AWS_DEFAULT_REGION": region}
        self.aws = workspace / "aws"
        self.aws.mkdir(exist_ok=True, mode=0o700)
        state = load_state(workspace)
        self.profiles = {"project_profile": project_profile, "governance_profile": governance_profile, "region": region}
        bound = state.get("profiles", self.profiles)
        if any(bound[key] != self.profiles[key] for key in (("project_profile", "region") if read_only else self.profiles)):
            raise ValueError("Use the profiles/region bound to this workspace")
        if read_only:
            return
        metadata = read(repo / "terraform/.terraform/terraform.tfstate")["backend"]
        config = metadata["config"]
        if (metadata["type"] != "s3" or config["key"] != "control-plane/terraform.tfstate" or
                config["bucket"] != f"aws-lakehouse-platform-{self.account}-{region}-tfstate" or config["region"] != region):
            raise ValueError("Initialize the exact project's versioned S3 backend before using the AWS journey")

    def command(self, args, log):
        log = self.workspace / log
        log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        errors = log.with_suffix(log.suffix + ".stderr.log")
        log.touch(mode=0o600)
        errors.touch(mode=0o600)
        log.chmod(0o600)
        errors.chmod(0o600)
        with log.open("w") as stdout, errors.open("w") as stderr:
            started = time.monotonic()
            process = subprocess.Popen(args, cwd=self.repo, env=self.environment, stdout=stdout, stderr=stderr, text=True)
            while True:
                try:
                    code = process.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    print(f"[aws] {log.stem}: still running ({int(time.monotonic() - started)} seconds); private logs retained", flush=True)
        if code:
            raise RuntimeError("Command failed; inspect the private stage log before retrying")
        return log.read_text()

    def script(self, name, arguments, log):
        return self.command([sys.executable, "scripts/" + name, *map(str, arguments)], log)

    def values(self):
        values = {key: row["value"] for key, row in read(self.aws / "outputs.json").items()}
        build_integrated_invocations(values, "demo-validation", load_references=False)
        config = read(self.aws / "variables.tfvars.json")
        expected = "aws-lakehouse-platform-" + config["demo_id"]
        if values["glue_database_name"] != expected.replace("-", "_"):
            raise ValueError("Deployment outputs differ from the planned demo")
        if values["ingestion_access"]["role_arn"] != f"arn:aws:iam::{self.account}:role/{expected}-ingestion":
            raise ValueError("The planned dedicated ingestion role is required")
        return values

    def plan(self, var_file):
        state = load_state(self.workspace)
        if state["aws_status"] != "prepared":
            raise ValueError("Prepare a fresh workspace for a new plan")
        variables = read(var_file)
        if len(variables["demo_id"]) > 31:
            raise ValueError("Dedicated-role demonstration IDs must not exceed 31 characters")
        variables.update(aws_region=self.region, aws_profile=self.project_profile, ingestion_access_enabled=True,
                         ingestion_governance_profile=self.governance_profile, analyst_access_enabled=False)
        # The existing cleanup-context parser validates demo namespace/expiry.
        derive_cleanup_context({"resources": [{"type": "terraform_data", "name": "cleanup_context", "instances": [
            {"attributes": {"input": {"project": "aws-lakehouse-platform", "demo_id": variables["demo_id"], "expires_at": variables["expires_at"]}}}]}]})
        write(self.aws / "variables.tfvars.json", variables)
        plan = self.aws / "deploy.tfplan"
        self.command(["terraform", "-chdir=terraform", "plan", "-input=false", "-no-color",
                      "-var-file=" + str(self.aws / "variables.tfvars.json"), "-out=" + str(plan)], "aws/plan.log")
        data = json.loads(self.command(["terraform", "-chdir=terraform", "show", "-json", str(plan)], "aws/plan.json"))
        changes = validate_deployment_plan(data)
        write(self.aws / "plan-summary.json", changes)
        state.update(aws_status="planned", profiles=self.profiles, source_sha256=source_digest(self.repo),
                     plan_sha256=hashlib.sha256(plan.read_bytes()).hexdigest())
        write(self.workspace / "state.json", state)
        print(json.dumps(changes, indent=2), flush=True)

    def deploy(self):
        state = load_state(self.workspace)
        plan = self.aws / "deploy.tfplan"
        if (state["aws_status"] != "planned" or state["source_sha256"] != source_digest(self.repo) or
                state["plan_sha256"] != hashlib.sha256(plan.read_bytes()).hexdigest()):
            raise ValueError("Require the reviewed, unchanged plan and runtime sources")
        state["aws_status"] = "deploying"
        write(self.workspace / "state.json", state)
        self.command(["terraform", "-chdir=terraform", "apply", "-input=false", "-no-color", str(plan)], "aws/deploy.log")
        outputs = json.loads(self.command(["terraform", "-chdir=terraform", "output", "-json"], "aws/outputs.log"))
        write(self.aws / "outputs.json", outputs)
        self.values()
        state["aws_status"] = "deployed"
        write(self.workspace / "state.json", state)

    def analyst(self, values):
        import boto3
        credentials = self.operator.client("sts").assume_role(RoleArn=values["analyst_access"]["role_arn"],
            RoleSessionName="lakehouse-demo-analyst", DurationSeconds=900)["Credentials"]
        parameters = {"aws_access_key_id": credentials["AccessKeyId"], "aws_secret_access_key": credentials["SecretAccessKey"],
                      "aws_session_token": credentials["SessionToken"], "region_name": self.region}
        session = boto3.Session(**parameters)
        workgroup = validate_analyst_session(session, values, self.account)
        return session.client("athena"), workgroup

    def query(self, name, start, end, customer=None):
        if load_state(self.workspace)["aws_status"] != "completed":
            raise ValueError("Complete the governed AWS journey before interactive analyst queries")
        values = self.values()
        sql = render_query(name, values["glue_database_name"], start, end, customer)
        athena, workgroup = self.analyst(values)
        rows, statistics = query_rows(athena, values["glue_database_name"], workgroup, sql)
        if statistics.get("ResultReuseInformation", {}).get("ReusedPreviousResult") is not False:
            raise ValueError("Analyst result reuse must be explicitly false")
        return {"query": name, "start_inclusive": start, "end_exclusive": end, "origin": "AWS analyst",
                "rows": rows, "bytes_scanned": statistics.get("DataScannedInBytes", 0), "results_reused": False}

    def run(self):
        state = load_state(self.workspace)
        if state["aws_status"] != "deployed":
            raise ValueError("The AWS journey requires an unstarted deployed workspace")
        if state.get("source_sha256") != source_digest(self.repo):
            raise ValueError("Runtime sources changed since the deployment plan")
        values = self.values()
        lf = self.admin.client("lakeformation")
        baseline = lf.get_data_lake_settings()["DataLakeSettings"]
        write(self.aws / "lf-settings-before.json", baseline)
        def execute(phase, source, batches, references):
            values = self.values()
            replay = phase.endswith("replay")
            before = capture_replay_metadata(self.operator.client("glue"), values["glue_database_name"],
                         values["landing_path"].split("/")[2]) if replay else None
            args = ["--outputs", self.aws / "outputs.json", "--batch-dir", self.workspace / "batches" / source,
                    "--batch-id", state["run_id"] + "-" + source, "--gold-batch-id", state["run_id"] + "-" + phase,
                    "--project-profile", self.project_profile, "--region", self.region,
                    "--artifact-dir", self.aws / phase]
            if references: args.append("--load-references")
            self.script("run_integrated_batch.py", args, f"aws/{phase}-pipeline.log")
            if phase == "initial":
                self.script("enable_analyst_access.py", ["--var-file", self.aws / "variables.tfvars.json", "--artifact-dir", self.aws / "governance",
                    "--project-profile", self.project_profile, "--governance-profile", self.governance_profile,
                    "--region", self.region], "aws/governance.log")
                journal = read(self.aws / "governance/lf-bootstrap-journal.json")
                if not journal["restored"] or journal["before"] != baseline:
                    raise ValueError("Original Lake Formation settings were not restored")
                write(self.aws / "outputs.json", read(self.aws / "governance/outputs.json"))
                values = self.values()
            expected = self.aws / (phase + "-expected.json")
            write(expected, expected_products(batches))
            self.script("qualify_integrated_silver.py", [*batches, "--outputs", self.aws / "outputs.json",
                "--project-profile", self.project_profile, "--region", self.region, "--report", self.aws / (phase + "-silver.json")], f"aws/{phase}-silver.log")
            self.script("qualify_analyst_access.py", ["--outputs", self.aws / "outputs.json", "--expected", expected,
                "--bronze-probe-key", "landing/batch_id=" + state["run_id"] + "-initial/consumption_index.jsonl",
                "--project-profile", self.project_profile, "--governance-profile", self.governance_profile,
                "--region", self.region, "--report", self.aws / (phase + "-analyst.json")], f"aws/{phase}-analyst.log")
            silver, analyst = [read(self.aws / (phase + suffix)) for suffix in ("-silver.json", "-analyst.json")]
            if not silver["passed"] or not analyst["passed"]:
                raise ValueError("Silver or analyst audit failed")
            checks = {"Silver reconciliation": True, "Gold reconciliation": True,
                      "analyst refusals and SELECT restoration": True}
            if replay:
                stable = compare_replay(read(self.aws / "correction-silver.json"), silver, read(self.aws / "correction-analyst.json"), analyst,
                    before, capture_replay_metadata(self.operator.client("glue"), values["glue_database_name"], values["landing_path"].split("/")[2]))
                write(self.aws / (phase + "-stability.json"), stable)
                if not stable["passed"]: raise ValueError("Replay changed data contents or Iceberg metadata")
                checks["eleven data metadata identities stable"] = True
            athena, workgroup = self.analyst(values)
            products = {}
            for table, dimensions in DIMENSIONS.items():
                columns = ", ".join(f"CAST({field} AS varchar) AS {field}" for field in (*dimensions, "consumption_kwh", "energy_cost_eur"))
                products[table], statistics = query_rows(athena, values["glue_database_name"], workgroup,
                    f'SELECT {columns} FROM "{values["glue_database_name"]}"."{table}"')
                if statistics.get("ResultReuseInformation", {}).get("ReusedPreviousResult") is not False:
                    raise ValueError("Report must read fresh analyst results")
            if lf.get_data_lake_settings()["DataLakeSettings"] != baseline:
                raise ValueError("Lake Formation account settings drifted during the journey")
            return products, checks
        return run_scenarios(self.workspace, execute)

    def cleanup(self):
        """Delegate destruction to the existing pushed-revision native guard."""
        state = load_state(self.workspace)
        if state["aws_status"] in ("prepared", "planned", "cleaned"):
            raise ValueError("This workspace has no active deployment to clean")
        variables = read(self.aws / "variables.tfvars.json")
        current = json.loads(self.command(["terraform", "-chdir=terraform", "state", "pull"], "aws/pre-cleanup-state.json"))
        context = derive_cleanup_context(current)
        if (context.demo_id, context.expires_at) != (variables["demo_id"], variables["expires_at"]):
            raise ValueError("Cleanup state differs from this workspace; no build started")
        values = self.values() if (self.aws / "outputs.json").exists() else None
        if values:
            for job in {job for _, job, _ in build_integrated_invocations(values, "cleanup-check", load_references=True)}:
                from botocore.exceptions import ClientError
                try:
                    page = self.admin.client("glue").get_job_runs(JobName=job, MaxResults=200)
                except ClientError as error:
                    if error.response["Error"]["Code"] == "EntityNotFoundException": continue
                    raise
                if page.get("NextToken") or any(row["JobRunState"] not in {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR", "EXPIRED"} for row in page["JobRuns"]):
                    raise ValueError("Inspect active/unknown managed runs before requesting destruction")
        client = self.admin.client("codebuild")
        if "cleanup_build_id" not in state:
            if state["aws_status"] == "cleaning":
                raise RuntimeError("Cleanup submission outcome is unknown; inspect CodeBuild before any new request")
            revision = self.command(["git", "rev-parse", "HEAD"], "aws/cleanup-revision.log").strip()
            if self.command(["git", "status", "--porcelain"], "aws/cleanup-git.log").strip():
                raise ValueError("Cleanup requires a clean pushed checkout")
            if not self.command(["git", "branch", "-r", "--contains", revision], "aws/cleanup-remote.log").strip():
                raise ValueError("Push this revision before native cleanup")
            state["aws_status"] = "cleaning"
            write(self.workspace / "state.json", state)
            result = client.start_build(projectName="aws-lakehouse-platform-demo-cleanup", sourceVersion=revision)["build"]
            state.update(aws_status="cleaning", cleanup_build_id=result["id"])
            write(self.workspace / "state.json", state)
        deadline = time.monotonic() + 1200
        next_notice = 0
        while True:
            build = client.batch_get_builds(ids=[state["cleanup_build_id"]])["builds"][0]
            write(self.aws / "cleanup-build.json", build)
            if time.monotonic() >= next_notice:
                print(f"[aws] cleanup: {build['buildStatus']}", flush=True)
                next_notice = time.monotonic() + 30
            if build["buildStatus"] == "SUCCEEDED": break
            if build["buildStatus"] in {"FAILED", "FAULT", "STOPPED", "TIMED_OUT"}:
                raise RuntimeError("Native cleanup failed; preserve its receipt and inspect before retrying")
            if time.monotonic() >= deadline:
                raise TimeoutError("Cleanup still active; invoke cleanup again to poll this same build")
            time.sleep(15)
        after = json.loads(self.command(["terraform", "-chdir=terraform", "state", "pull"], "aws/post-cleanup-state.json"))
        remaining = set()
        for resource in after["resources"]:
            for instance in resource.get("instances", []):
                address = resource["type"] + "." + resource["name"]
                if "index_key" in instance: address += "[" + json.dumps(instance["index_key"]) + "]"
                remaining.add(address)
        if remaining & ALLOWED_DELETE_ADDRESSES or derive_cleanup_context(after) != context:
            raise ValueError("Cleanup left data-plane state or changed the protected context")
        if values:
            bucket, database = values["landing_path"].split("/")[2], values["glue_database_name"]
            expect_absent(lambda: self.admin.client("s3").head_bucket(Bucket=bucket), {"404", "NoSuchBucket"})
            expect_absent(lambda: self.admin.client("glue").get_database(Name=database), {"EntityNotFoundException"})
            for job in {job for _, job, _ in build_integrated_invocations(values, "cleanup-check", load_references=True)}:
                expect_absent(lambda: self.admin.client("glue").get_job(JobName=job), {"EntityNotFoundException"})
            prefix = database.replace("_", "-")
            for suffix in ("ingestion", "analyst", "lakeformation-data"):
                expect_absent(lambda: self.admin.client("iam").get_role(RoleName=prefix + "-" + suffix), {"NoSuchEntity"})
            for role in ("aws-lakehouse-platform-demo-terraform-operator", "aws-lakehouse-platform-demo-glue-silver"):
                self.admin.client("iam").get_role(RoleName=role)
            self.admin.client("iam").get_user(UserName=variables.get("operator_iam_user_name", "lakehouse_user"))
            versioning = self.admin.client("s3").get_bucket_versioning(Bucket=f"aws-lakehouse-platform-{self.account}-{self.region}-tfstate")
            if versioning.get("Status") != "Enabled":
                raise ValueError("Protected state bucket must remain versioned")
        if (self.aws / "lf-settings-before.json").exists():
            if self.admin.client("lakeformation").get_data_lake_settings()["DataLakeSettings"] != read(self.aws / "lf-settings-before.json"):
                raise ValueError("Original LF account settings differ after cleanup")
        state["aws_status"] = "cleaned"
        write(self.workspace / "state.json", state)
        write(self.workspace / "cleanup-receipt.json", {"native_build_succeeded": True, "data_plane_absent_from_state": True,
              "cleanup_context_preserved": True, "independent_bucket_database_jobs_roles_API_checks": bool(values),
              "persistent_identities_and_versioned_state_verified": bool(values)})
