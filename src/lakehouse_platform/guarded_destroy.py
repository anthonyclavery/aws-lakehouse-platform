"""Bounded targeted deletion, including LF self-revoke partial-effect recovery."""
import json
from pathlib import Path
import re
import subprocess

from .cleanup_guard import ALLOWED_DELETE_ADDRESSES, validate_destroy_plan


def lf_revoke_only_failure(output):
    clean = re.sub(r"\x1b\[[0-9;]*m", "", output)
    errors = re.findall(r"Error:\s*([^\n]+)", clean)
    return bool(errors) and all(line.startswith("unable to revoke LakeFormation Permissions") for line in errors)


def deletion_set(plan):
    return {r["address"] for r in plan.get("resource_changes", []) if r["change"]["actions"] == ["delete"]}


def validate_recovery(initial, refreshed):
    before, after = deletion_set(initial), deletion_set(refreshed)
    if not after < before or any(address.startswith("aws_lakeformation_permissions.") for address in after):
        raise RuntimeError("Retry requires strict deletion progress and no remaining LF grant revocations")


def destroy(region, demo_id, expires_at, *, directory=Path("/tmp"), run=subprocess.run):
    directory.mkdir(parents=True, exist_ok=True)
    plan_path = directory / "destroy.tfplan"
    show_path = directory / "destroy-plan.json"
    base = ["terraform", "-chdir=terraform"]
    def prepare():
        command = base + ["plan", "-destroy", "-input=false", "-lock-timeout=5m", f"-out={plan_path}",
                          f"-var=aws_region={region}", f"-var=demo_id={demo_id}", f"-var=expires_at={expires_at}"]
        command.extend(f"-target={address}" for address in sorted(ALLOWED_DELETE_ADDRESSES))
        result = run(command, capture_output=True, text=True)
        print(result.stdout + result.stderr, flush=True)
        if result.returncode:
            raise RuntimeError("Targeted destruction plan failed")
        shown = run(base + ["show", "-json", str(plan_path)], capture_output=True, text=True)
        if shown.returncode:
            raise RuntimeError("Unable to read the saved destroy plan")
        plan = json.loads(shown.stdout)
        show_path.write_text(shown.stdout)
        validate_destroy_plan(plan, demo_id, expires_at)
        return plan
    initial = prepare()
    for attempt in (1, 2):
        result = run(base + ["apply", "-input=false", str(plan_path)], capture_output=True, text=True)
        output = result.stdout + result.stderr
        print(output, flush=True)
        if result.returncode == 0:
            return {"succeeded": True, "passes": attempt}
        if attempt == 2 or not lf_revoke_only_failure(output):
            raise RuntimeError("Guarded destroy apply failed; no unverified retry is allowed")
        # LF can remove a caller's own grants and still return AccessDenied while
        # resolving that now-invisible resource. Never infer success from an
        # error: refresh state and validate a new, strictly smaller delete plan.
        refreshed = prepare()
        validate_recovery(initial, refreshed)
        print("All failed LF revocations disappeared on refresh; applying the revalidated remaining subset", flush=True)
    raise AssertionError("Unreachable")
