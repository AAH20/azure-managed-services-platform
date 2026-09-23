"""Azure VM backup incident collection, controlled retry, and outcome verification."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256

from .azure_evidence import AzureCliRunner, CommandRunner, collect

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
APPROVERS = {"workload-owner", "backup-operator"}


class BackupResolutionError(ValueError):
    """Missing evidence, invalid scope, or insufficient action authority."""


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise BackupResolutionError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BackupResolutionError("timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise BackupResolutionError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _field(value: object, label: str) -> str:
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise BackupResolutionError(f"invalid {label}")
    return value


def validate_scope(value: dict) -> dict:
    if not isinstance(value, dict):
        raise BackupResolutionError("scope must be an object")
    for key in ("incident_id", "tenant_id", "subscription_id", "resource_group",
                "vault_name", "vm_name"):
        _field(value.get(key), key)
    return {key: value[key] for key in ("incident_id", "tenant_id", "subscription_id",
                                        "resource_group", "vault_name", "vm_name")}


def _properties(value: dict) -> dict:
    props = value.get("properties", {})
    return props if isinstance(props, dict) else {}


def _jobs(raw: object, vm_name: str) -> list[dict]:
    if not isinstance(raw, list):
        raise BackupResolutionError("job response must be a list")
    jobs = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        props = _properties(item)
        name = props.get("entityFriendlyName", item.get("entityFriendlyName"))
        if name != vm_name:
            continue
        job = {"job_id": item.get("name"),
               "operation": props.get("operation", item.get("operation")),
               "status": props.get("status", item.get("status")),
               "started_at": props.get("startTime", item.get("startTime")),
               "ended_at": props.get("endTime", item.get("endTime")),
               "error_code": props.get("errorCode", item.get("errorCode"))}
        if (isinstance(job["job_id"], str) and isinstance(job["operation"], str)
                and isinstance(job["status"], str) and isinstance(job["started_at"], str)):
            _time(job["started_at"])
            jobs.append(job)
    return jobs


def _points(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise BackupResolutionError("recovery point response must be a list")
    points = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        props = _properties(item)
        point_id = item.get("name")
        created = props.get("recoveryPointTime", item.get("recoveryPointTime"))
        if isinstance(point_id, str) and isinstance(created, str):
            _time(created)
            points.append({"point_id": point_id, "created_at": created})
    return points


def collect_snapshot(scope: dict, runner: CommandRunner | None = None) -> dict:
    scope = validate_scope(scope)
    executor = runner or AzureCliRunner()
    common = ["--resource-group", scope["resource_group"], "--vault-name", scope["vault_name"]]
    item_args = ["--container-name", scope["vm_name"], "--backup-management-type",
                 "AzureIaasVM"]
    specs = (
        ("account", "account-show-v1", ["account", "show"]),
        ("backup-jobs", "backup-jobs-v1", ["backup", "job", "list", *common,
                                           "--backup-management-type", "AzureIaasVM",
                                           "--operation", "Backup"]),
        ("backup-item", "backup-item-v1", ["backup", "item", "show", *common,
                                           *item_args, "--name", scope["vm_name"]]),
        ("recovery-points", "recovery-points-v1", ["backup", "recoverypoint", "list",
                                                 *common, *item_args, "--item-name",
                                                 scope["vm_name"]]),
    )
    observations = [collect(executor, source=source, tenant_id=scope["tenant_id"],
                            subscription_id=scope["subscription_id"], query_id=query,
                            arguments=args) for source, query, args in specs]
    account = observations[0].data
    account_verified = (observations[0].status == "observed" and isinstance(account, dict)
                        and account.get("id", "").lower() == scope["subscription_id"].lower()
                        and account.get("tenantId", "").lower() == scope["tenant_id"].lower())
    if not account_verified or any(item.status != "observed" for item in observations):
        return {"schema_version": "1.0", "scope": scope, "source_status": "unknown",
                "account_verified": account_verified,
                "jobs": [], "item": None, "recovery_points": [],
                "receipts": [item.receipt.to_dict() for item in observations],
                "collected_at": datetime.now(UTC).isoformat()}
    item = observations[2].data
    if not isinstance(item, dict):
        raise BackupResolutionError("backup item response must be an object")
    return {"schema_version": "1.0", "scope": scope, "source_status": "observed",
            "account_verified": True,
            "jobs": _jobs(observations[1].data, scope["vm_name"]), "item": item,
            "recovery_points": _points(observations[3].data),
            "receipts": [item.receipt.to_dict() for item in observations],
            "collected_at": datetime.now(UTC).isoformat()}


def _latest_backup(snapshot: dict) -> dict | None:
    jobs = [job for job in snapshot["jobs"] if job["operation"].startswith("Backup")]
    return max(jobs, key=lambda job: _time(job["started_at"])) if jobs else None


def propose(snapshot: dict) -> dict:
    scope = validate_scope(snapshot.get("scope"))
    if snapshot.get("source_status") != "observed" or not isinstance(snapshot.get("item"), dict):
        action, reason, job = "manual_review", "backup evidence is unavailable", None
    else:
        job = _latest_backup(snapshot)
        if job is None:
            action, reason = "manual_review", "no matching backup job was observed"
        elif job["status"] == "Failed":
            action, reason = "backup_now_candidate", "latest matching backup job failed; diagnose cause before retry"
        else:
            action, reason = "manual_review", f"latest matching backup job is {job['status']}"
    digest = sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return {"schema_version": "1.0", "scope": scope, "action": action, "reason": reason,
            "failed_job_id": job["job_id"] if action == "backup_now_candidate" else None,
            "error_code": job.get("error_code") if job else None,
            "snapshot_sha256": digest, "required_approvers": sorted(APPROVERS),
            "cloud_mutations_executed": 0,
            "boundary": "A failed job does not prove cause. Retry is a candidate for operator review, not an automatic repair."}


def retry_backup(scope: dict, plan: dict, current: dict, approvals: set[str],
                 runner: CommandRunner) -> dict:
    scope = validate_scope(scope)
    if plan.get("scope") != scope or current.get("scope") != scope:
        raise BackupResolutionError("scope mismatch")
    if plan.get("action") != "backup_now_candidate":
        raise BackupResolutionError("plan does not permit a backup retry")
    if current.get("account_verified") is not True:
        raise BackupResolutionError("Azure account scope is not verified")
    missing = APPROVERS - approvals
    if missing:
        raise BackupResolutionError(f"missing required approvals: {sorted(missing)}")
    fresh = propose(current)
    if fresh["action"] != "backup_now_candidate" or fresh["failed_job_id"] != plan.get("failed_job_id"):
        raise BackupResolutionError("current backup state changed; collect and review again")
    started = datetime.now(UTC).isoformat()
    args = ["backup", "protection", "backup-now", "--resource-group", scope["resource_group"],
            "--vault-name", scope["vault_name"], "--container-name", scope["vm_name"],
            "--item-name", scope["vm_name"], "--backup-management-type", "AzureIaasVM",
            "--subscription", scope["subscription_id"], "--output", "json"]
    result = runner.run(args)
    try:
        response = json.loads(result.stdout) if result.returncode == 0 else {}
    except json.JSONDecodeError:
        response = {}
    job_id = response.get("name") if isinstance(response, dict) else None
    if not isinstance(job_id, str) or not job_id:
        job_id = None
    return {"schema_version": "1.0", "scope": scope, "action": "backup-now",
            "started_at": started, "cli_returncode": result.returncode,
            "request_status": "accepted" if result.returncode == 0 else "unknown",
            "request_job_id": job_id,
            "mutation_attempted": True,
            "boundary": "CLI acceptance is not a completed backup. Verification requires the returned job ID, a later completed job, and a new recovery point."}


def verify(before: dict, after: dict, attempt: dict) -> dict:
    scope = validate_scope(before.get("scope"))
    if after.get("scope") != scope or attempt.get("scope") != scope:
        raise BackupResolutionError("scope mismatch")
    if before.get("source_status") != "observed" or after.get("source_status") != "observed":
        return {"status": "unknown", "reason": "read-only evidence is incomplete"}
    if attempt.get("request_status") != "accepted":
        return {"status": "unknown", "reason": "backup request acceptance is unknown"}
    job_id = attempt.get("request_job_id")
    if not isinstance(job_id, str) or not job_id:
        return {"status": "unknown", "reason": "backup request job ID is unavailable"}
    started = _time(attempt.get("started_at"))
    old_points = {point["point_id"] for point in before["recovery_points"]}
    new_points = [point for point in after["recovery_points"] if point["point_id"] not in old_points
                  and _time(point["created_at"]) >= started]
    completed_jobs = [job for job in after["jobs"] if job["job_id"] == job_id
                      and job["operation"].startswith("Backup")
                      and job["status"] == "Completed" and _time(job["started_at"]) >= started]
    passed = bool(new_points and completed_jobs)
    return {"schema_version": "1.0", "scope": scope,
            "status": "verified_backup" if passed else "unverified",
            "new_recovery_point_ids": sorted(point["point_id"] for point in new_points),
            "completed_job_ids": sorted(job["job_id"] for job in completed_jobs),
            "restorability_proven": False,
            "boundary": "A new recovery point and completed job do not prove an actual restore; run a separate isolated restore drill."}
